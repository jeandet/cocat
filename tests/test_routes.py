from pathlib import Path

import anyio
import pytest
from conftest import (
    AppClient,
    MailClient,
    assert_websocket_refused,
    login,
    make_user,
    ws_headers,
)
from fastapi.testclient import TestClient
from pycrdt import (
    Doc,
    Map,
    YMessageType,
    YSyncMessageType,
    create_sync_message,
    create_update_message,
    handle_sync_message,
)
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.websockets import WebSocketDisconnect
from utils import RecordingMail, clean_nicegui, in_session, user_by_email

from cocat.app.app import CocatApp
from cocat.app.db import Role
from cocat.app.rooms import add_member
from cocat.app.storage import room_file


def create_room(app: AppClient | MailClient, name: str = "Lab") -> dict:
    response = app.client.post("/api/rooms", json={"name": name})
    assert response.status_code == 201
    return response.json()


def invite(app: AppClient, room_id: str) -> str:
    response = app.client.post(f"/api/rooms/{room_id}/invitations", json={})
    assert response.status_code == 201
    return response.json()["token"]


def list_members(app: AppClient, room_id: str) -> list[dict]:
    response = app.client.get(f"/api/rooms/{room_id}/members")
    assert response.status_code == 200
    return response.json()


def join_room(app: AppClient, email: str, token: str) -> None:
    login(app.client, email)
    response = app.client.post("/api/invitations/accept", json={"token": token})
    assert response.status_code == 200


def test_create_list_rename_delete_room(app_client):
    make_user(app_client.db_path, "owner@x.org")
    login(app_client.client, "owner@x.org")

    me = app_client.client.get("/api/me")
    assert me.status_code == 200
    assert me.json()["email"] == "owner@x.org"

    room = create_room(app_client)
    assert room["name"] == "Lab"
    assert room["role"] == "owner"
    listed = {
        listed["id"]: listed for listed in app_client.client.get("/api/rooms").json()
    }
    assert len(listed) == 2
    assert listed[room["id"]] == room

    renamed = app_client.client.patch(
        f"/api/rooms/{room['id']}", json={"name": "Renamed"}
    )
    assert renamed.status_code == 200
    assert renamed.json() == {**room, "name": "Renamed"}

    assert app_client.client.delete(f"/api/rooms/{room['id']}").status_code == 204
    assert [
        listed["name"] for listed in app_client.client.get("/api/rooms").json()
    ] == ["Personal"]


def test_invite_link_flow(app_client):
    make_user(app_client.db_path, "owner@x.org")
    make_user(app_client.db_path, "guest@x.org")
    login(app_client.client, "owner@x.org")
    room = create_room(app_client)
    token = invite(app_client, room["id"])

    app_client.client.cookies.clear()
    preview = app_client.client.post("/api/invitations/preview", json={"token": token})
    assert preview.status_code == 200
    assert preview.json()["room_id"] == room["id"]
    assert preview.json()["room_name"] == "Lab"
    assert preview.json()["role"] == "editor"
    assert preview.json()["inviter_email"] == "owner@x.org"
    assert preview.json()["expires_at"]

    join_room(app_client, "guest@x.org", token)

    rooms = {
        listed["id"]: listed for listed in app_client.client.get("/api/rooms").json()
    }
    assert len(rooms) == 2
    assert rooms[room["id"]] == {"id": room["id"], "name": "Lab", "role": "editor"}


def test_member_management(app_client):
    make_user(app_client.db_path, "owner@x.org")
    make_user(app_client.db_path, "editor@x.org")
    login(app_client.client, "owner@x.org")
    room = create_room(app_client)
    token = invite(app_client, room["id"])
    join_room(app_client, "editor@x.org", token)

    login(app_client.client, "owner@x.org")
    emails = {
        member["email"]: member for member in list_members(app_client, room["id"])
    }
    assert set(emails) == {"owner@x.org", "editor@x.org"}
    owner_id = emails["owner@x.org"]["user_id"]
    editor_id = emails["editor@x.org"]["user_id"]
    assert emails["owner@x.org"]["role"] == "owner"

    member_url = f"/api/rooms/{room['id']}/members/{editor_id}"
    assert (
        app_client.client.patch(member_url, json={"role": "editor"}).status_code == 204
    )
    owner_url = f"/api/rooms/{room['id']}/owner"
    assert (
        app_client.client.post(owner_url, json={"user_id": editor_id}).status_code
        == 204
    )

    login(app_client.client, "editor@x.org")
    assert (
        app_client.client.delete(
            f"/api/rooms/{room['id']}/members/{owner_id}"
        ).status_code
        == 204
    )

    invitations = app_client.client.get(f"/api/rooms/{room['id']}/invitations")
    assert invitations.status_code == 200
    assert [invitation["role"] for invitation in invitations.json()] == ["editor"]
    invitation_id = invitations.json()[0]["id"]
    revoke_url = f"/api/rooms/{room['id']}/invitations/{invitation_id}"
    assert app_client.client.delete(revoke_url).status_code == 204
    assert app_client.client.get(f"/api/rooms/{room['id']}/invitations").json() == []


def test_errors_map_to_status(app_client):
    for email in ("owner@x.org", "editor@x.org", "stranger@x.org"):
        make_user(app_client.db_path, email)
    login(app_client.client, "owner@x.org")
    room = create_room(app_client)
    token = invite(app_client, room["id"])
    join_room(app_client, "editor@x.org", token)

    login(app_client.client, "editor@x.org")
    renamed = app_client.client.patch(f"/api/rooms/{room['id']}", json={"name": "X"})
    assert renamed.status_code == 403

    login(app_client.client, "stranger@x.org")
    members_url = f"/api/rooms/{room['id']}/members"
    assert app_client.client.get(members_url).status_code == 404
    assert app_client.client.get("/api/rooms/missing/members").status_code == 404

    login(app_client.client, "owner@x.org")
    owner_id = app_client.client.get("/api/me").json()["id"]
    leaving = app_client.client.delete(f"{members_url}/{owner_id}")
    assert leaving.status_code == 409


def test_blank_room_name_is_a_bad_request(app_client):
    make_user(app_client.db_path, "owner@x.org")
    login(app_client.client, "owner@x.org")

    response = app_client.client.post("/api/rooms", json={"name": "   "})

    assert response.status_code == 400


def test_the_owner_can_assign_the_viewer_role(app_client):
    make_user(app_client.db_path, "owner@x.org")
    make_user(app_client.db_path, "member@x.org")
    login(app_client.client, "owner@x.org")
    room = create_room(app_client)
    token = invite(app_client, room["id"])
    join_room(app_client, "member@x.org", token)
    login(app_client.client, "owner@x.org")
    member = member_id_of(app_client, room["id"], "member@x.org")

    response = app_client.client.patch(
        f"/api/rooms/{room['id']}/members/{member}", json={"role": "viewer"}
    )

    assert response.status_code == 204
    roles = [
        row["role"]
        for row in list_members(app_client, room["id"])
        if row["email"] == "member@x.org"
    ]
    assert roles == ["viewer"]


def test_the_owner_role_is_still_not_assignable(app_client):
    make_user(app_client.db_path, "owner@x.org")
    make_user(app_client.db_path, "member@x.org")
    login(app_client.client, "owner@x.org")
    room = create_room(app_client)
    token = invite(app_client, room["id"])
    join_room(app_client, "member@x.org", token)
    login(app_client.client, "owner@x.org")
    member = member_id_of(app_client, room["id"], "member@x.org")

    response = app_client.client.patch(
        f"/api/rooms/{room['id']}/members/{member}", json={"role": "owner"}
    )

    assert response.status_code == 422


def test_an_invitation_can_grant_the_viewer_role(app_client):
    make_user(app_client.db_path, "owner@x.org")
    make_user(app_client.db_path, "guest@x.org")
    login(app_client.client, "owner@x.org")
    room = create_room(app_client)
    response = app_client.client.post(
        f"/api/rooms/{room['id']}/invitations", json={"role": "viewer"}
    )
    assert response.status_code == 201
    token = response.json()["token"]

    preview = app_client.client.post("/api/invitations/preview", json={"token": token})
    assert preview.status_code == 200
    assert preview.json()["role"] == "viewer"

    join_room(app_client, "guest@x.org", token)
    rooms = app_client.client.get("/api/rooms").json()
    assert [
        (room_["id"], room_["role"]) for room_ in rooms if room_["id"] == room["id"]
    ] == [(room["id"], "viewer")]


def test_invitation_token_never_in_a_path(app_client):
    paths = app_client.client.get("/openapi.json").json()["paths"]

    assert paths
    assert all("{token}" not in path for path in paths)
    assert all("token" not in path for path in paths if path.startswith("/api"))


def test_new_user_gets_personal_room_not_email_prefix(app_client):
    make_user(app_client.db_path, "john@a.org")
    make_user(app_client.db_path, "john@b.org")

    login(app_client.client, "john@a.org")
    rooms_a = app_client.client.get("/api/rooms").json()
    login(app_client.client, "john@b.org")
    rooms_b = app_client.client.get("/api/rooms").json()

    assert len(rooms_a) == 1
    assert len(rooms_b) == 1
    assert rooms_a[0]["name"] == "Personal"
    assert rooms_a[0]["id"] != "john"
    assert rooms_a[0]["id"] != rooms_b[0]["id"]
    members_url = "/api/rooms/{}/members"
    assert (
        app_client.client.get(members_url.format(rooms_a[0]["id"])).status_code == 404
    )

    login(app_client.client, "john@a.org")
    assert (
        app_client.client.get(members_url.format(rooms_b[0]["id"])).status_code == 404
    )


def test_legacy_rooms_endpoint_shape(app_client):
    make_user(app_client.db_path, "john@a.org")
    login(app_client.client, "john@a.org")
    personal = app_client.client.get("/api/rooms").json()[0]

    response = app_client.client.get("/rooms")

    assert response.status_code == 200
    assert response.json() == {"rooms": [personal["id"]]}


def test_websocket_member_accepted_nonmember_refused(app_client):
    make_user(app_client.db_path, "owner@x.org")
    make_user(app_client.db_path, "stranger@x.org")
    login(app_client.client, "owner@x.org")
    room = create_room(app_client)

    with app_client.client.websocket_connect(
        f"/room/{room['id']}", headers=ws_headers(app_client.client)
    ):
        pass

    login(app_client.client, "stranger@x.org")
    assert_websocket_refused(
        app_client.client,
        f"/room/{room['id']}",
        ws_headers(app_client.client),
    )


def test_websocket_does_not_hold_a_db_connection(tmp_path):
    update_dir = str(tmp_path / "update_dir")
    Path(update_dir).mkdir()
    db_path = str(tmp_path / "test.db")
    with clean_nicegui():
        cocat_app = CocatApp(update_dir, db_path)
        with TestClient(cocat_app.app, base_url="https://testserver") as client:
            app_client = AppClient(
                client=client, db_path=db_path, update_dir=update_dir
            )
            make_user(db_path, "member@x.org")
            login(client, "member@x.org")
            room = create_room(app_client)

            with client.websocket_connect(
                f"/room/{room['id']}", headers=ws_headers(client)
            ):
                assert cocat_app.db.engine.pool.checkedout() == 0


def test_removed_member_is_disconnected_and_cannot_reconnect(app_client):
    make_user(app_client.db_path, "owner@x.org")
    make_user(app_client.db_path, "member@x.org")
    login(app_client.client, "owner@x.org")
    room = create_room(app_client)
    token = invite(app_client, room["id"])
    join_room(app_client, "member@x.org", token)
    member_id = next(
        member["user_id"]
        for member in list_members(app_client, room["id"])
        if member["email"] == "member@x.org"
    )

    login(app_client.client, "member@x.org")
    with app_client.client.websocket_connect(
        f"/room/{room['id']}", headers=ws_headers(app_client.client)
    ) as websocket:
        assert websocket.receive_bytes()

        login(app_client.client, "owner@x.org")
        response = app_client.client.delete(
            f"/api/rooms/{room['id']}/members/{member_id}"
        )
        assert response.status_code == 204

        with pytest.raises(WebSocketDisconnect) as excinfo:
            websocket.receive_bytes()
        assert excinfo.value.code == 1008

    login(app_client.client, "member@x.org")
    assert_websocket_refused(
        app_client.client,
        f"/room/{room['id']}",
        ws_headers(app_client.client),
    )


def test_delete_room_disconnects_and_removes_file(app_client):
    make_user(app_client.db_path, "owner@x.org")
    login(app_client.client, "owner@x.org")
    room = create_room(app_client)
    file_path = room_file(app_client.update_dir, room["id"])

    with app_client.client.websocket_connect(
        f"/room/{room['id']}", headers=ws_headers(app_client.client)
    ) as websocket:
        assert websocket.receive_bytes()
        assert file_path.exists()

        response = app_client.client.delete(f"/api/rooms/{room['id']}")
        assert response.status_code == 204

        with pytest.raises(WebSocketDisconnect) as excinfo:
            websocket.receive_bytes()
        assert excinfo.value.code == 1008

    assert not file_path.exists()


def test_delete_room_disconnects_every_member(app_client):
    make_user(app_client.db_path, "owner@x.org")
    make_user(app_client.db_path, "member@x.org")
    login(app_client.client, "owner@x.org")
    room = create_room(app_client)
    token = invite(app_client, room["id"])
    join_room(app_client, "member@x.org", token)
    member_headers = ws_headers(app_client.client)

    login(app_client.client, "owner@x.org")
    url = f"/room/{room['id']}"
    with (
        app_client.client.websocket_connect(
            url, headers=ws_headers(app_client.client)
        ) as owner_socket,
        app_client.client.websocket_connect(
            url, headers=member_headers
        ) as member_socket,
    ):
        assert owner_socket.receive_bytes()
        assert member_socket.receive_bytes()

        deleted = app_client.client.delete(f"/api/rooms/{room['id']}")
        assert deleted.status_code == 204

        for socket in (owner_socket, member_socket):
            with pytest.raises(WebSocketDisconnect) as excinfo:
                socket.receive_bytes()
            assert excinfo.value.code == 1008


def test_an_invitation_email_is_sent(app_client_mail: MailClient) -> None:
    make_user(app_client_mail.db_path, "owner@example.org")
    login(app_client_mail.client, "owner@example.org")
    room = create_room(app_client_mail)

    response = app_client_mail.client.post(
        f"/api/rooms/{room['id']}/invitations", json={"email": "guest@example.org"}
    )

    assert response.status_code == 201
    assert response.json()["token"] == ""
    [(_to, _subject, body)] = app_client_mail.mail.mails_to("guest@example.org")
    assert "/ui/invitation#" in body


def test_an_invitation_returns_its_token_without_a_mailer(
    app_client: AppClient,
) -> None:
    make_user(app_client.db_path, "owner@example.org")
    login(app_client.client, "owner@example.org")
    room = create_room(app_client)

    response = app_client.client.post(
        f"/api/rooms/{room['id']}/invitations", json={"email": "guest@example.org"}
    )

    assert response.status_code == 201
    assert response.json()["token"]


def test_a_placeholder_address_cannot_register(app_client: AppClient) -> None:
    response = app_client.client.post(
        "/auth/register",
        json={"email": "orcid-1@orcid.placeholder.cocat.org", "password": "pwd123"},
    )

    assert response.status_code == 422


def test_a_mailer_needs_a_public_url(
    update_dir: str, db_path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("COCAT_PUBLIC_URL", raising=False)

    with clean_nicegui():
        with pytest.raises(ValueError, match="COCAT_PUBLIC_URL"):
            CocatApp(update_dir, db_path, mail=RecordingMail())


def document_update() -> bytes:
    document: Doc = Doc()
    content: Map = document.get("content", type=Map)
    content["key"] = "value"
    return create_update_message(document.get_update())


def content_after_sync_request(websocket) -> dict:
    websocket.send_bytes(create_sync_message(Doc()))
    while True:
        frame = websocket.receive_bytes()
        if frame[0] != YMessageType.SYNC or frame[1] != YSyncMessageType.SYNC_STEP2:
            continue
        document: Doc = Doc()
        content: Map = document.get("content", type=Map)
        handle_sync_message(frame[1:], document)
        return {"key": content["key"]} if "key" in content else {}


def add_member_with(app: AppClient, room_id: str, email: str, role: Role) -> None:
    make_user(app.db_path, email)

    async def add(session: AsyncSession) -> None:
        member = await user_by_email(session, email)
        await add_member(session, room_id, member.id, role)

    anyio.run(in_session, app.db_path, add)


def test_a_viewer_cannot_write_to_the_room(app_client: AppClient) -> None:
    make_user(app_client.db_path, "owner@x.org")
    login(app_client.client, "owner@x.org")
    room = create_room(app_client)
    add_member_with(app_client, room["id"], "viewer@x.org", Role.viewer)

    login(app_client.client, "viewer@x.org")
    with app_client.client.websocket_connect(
        f"/room/{room['id']}", headers=ws_headers(app_client.client)
    ) as websocket:
        websocket.receive_bytes()

        websocket.send_bytes(document_update())

        assert content_after_sync_request(websocket) == {}


def test_an_editor_can_write_to_the_room(app_client: AppClient) -> None:
    make_user(app_client.db_path, "owner@x.org")
    login(app_client.client, "owner@x.org")
    room = create_room(app_client)
    add_member_with(app_client, room["id"], "editor@x.org", Role.editor)

    login(app_client.client, "editor@x.org")
    with app_client.client.websocket_connect(
        f"/room/{room['id']}", headers=ws_headers(app_client.client)
    ) as websocket:
        websocket.receive_bytes()

        websocket.send_bytes(document_update())

        assert content_after_sync_request(websocket) == {"key": "value"}


def content_of(frame: bytes) -> dict:
    assert frame[0] == YMessageType.SYNC
    assert frame[1] == YSyncMessageType.SYNC_UPDATE
    document: Doc = Doc()
    content: Map = document.get("content", type=Map)
    handle_sync_message(frame[1:], document)
    return {"key": content["key"]} if "key" in content else {}


def test_a_viewer_still_receives_an_update_from_the_owner(
    app_client: AppClient,
) -> None:
    make_user(app_client.db_path, "owner@x.org")
    login(app_client.client, "owner@x.org")
    room = create_room(app_client)
    add_member_with(app_client, room["id"], "viewer@x.org", Role.viewer)
    owner_headers = ws_headers(app_client.client)

    login(app_client.client, "viewer@x.org")
    with app_client.client.websocket_connect(
        f"/room/{room['id']}", headers=ws_headers(app_client.client)
    ) as viewer:
        viewer.receive_bytes()

        with app_client.client.websocket_connect(
            f"/room/{room['id']}", headers=owner_headers
        ) as owner:
            owner.receive_bytes()

            owner.send_bytes(document_update())

            assert content_of(viewer.receive_bytes()) == {"key": "value"}


def member_id_of(app: AppClient, room_id: str, email: str) -> str:
    return next(
        member["user_id"]
        for member in list_members(app, room_id)
        if member["email"] == email
    )


def test_changing_a_members_role_closes_their_socket(app_client):
    make_user(app_client.db_path, "owner@x.org")
    make_user(app_client.db_path, "member@x.org")
    login(app_client.client, "owner@x.org")
    room = create_room(app_client)
    join_room(app_client, "member@x.org", invite(app_client, room["id"]))
    member_id = member_id_of(app_client, room["id"], "member@x.org")

    login(app_client.client, "member@x.org")
    with app_client.client.websocket_connect(
        f"/room/{room['id']}", headers=ws_headers(app_client.client)
    ) as websocket:
        assert websocket.receive_bytes()

        login(app_client.client, "owner@x.org")
        response = app_client.client.patch(
            f"/api/rooms/{room['id']}/members/{member_id}", json={"role": "editor"}
        )
        assert response.status_code == 204

        with pytest.raises(WebSocketDisconnect) as excinfo:
            websocket.receive_bytes()
        assert excinfo.value.code == 1008


def test_changing_a_role_does_not_close_other_members(app_client):
    make_user(app_client.db_path, "owner@x.org")
    make_user(app_client.db_path, "member@x.org")
    make_user(app_client.db_path, "other@x.org")
    login(app_client.client, "owner@x.org")
    room = create_room(app_client)
    tokens = [invite(app_client, room["id"]) for _ in range(2)]
    for email, token in zip(("member@x.org", "other@x.org"), tokens):
        join_room(app_client, email, token)
    member_id = member_id_of(app_client, room["id"], "member@x.org")

    login(app_client.client, "other@x.org")
    with app_client.client.websocket_connect(
        f"/room/{room['id']}", headers=ws_headers(app_client.client)
    ) as websocket:
        assert websocket.receive_bytes()

        login(app_client.client, "owner@x.org")
        response = app_client.client.patch(
            f"/api/rooms/{room['id']}/members/{member_id}", json={"role": "editor"}
        )
        assert response.status_code == 204

        websocket.send_bytes(create_sync_message(Doc()))
        assert websocket.receive_bytes()
