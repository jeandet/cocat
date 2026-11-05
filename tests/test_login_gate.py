import anyio
import httpx
from conftest import AppClient, MailClient, make_user
from utils import set_verified

UNVERIFIED = "unverified@example.org"


def make_unverified(db_path: str) -> None:
    """A CLI account is verified, so drop it back to unverified on purpose."""
    make_user(db_path, UNVERIFIED)
    anyio.run(set_verified, db_path, UNVERIFIED, False)


def log_in(client: AppClient | MailClient) -> httpx.Response:
    return client.client.post(
        "/auth/jwt/login", data={"username": UNVERIFIED, "password": "pwd"}
    )


def test_without_a_mailer_an_unverified_account_logs_in(
    app_client: AppClient,
) -> None:
    make_unverified(app_client.db_path)

    response = log_in(app_client)

    assert response.status_code == 204


def test_with_a_mailer_an_unverified_account_is_refused(
    app_client_mail: MailClient,
) -> None:
    make_unverified(app_client_mail.db_path)

    response = log_in(app_client_mail)

    assert response.status_code == 400
    assert response.json() == {"detail": "LOGIN_USER_NOT_VERIFIED"}


def test_a_verified_account_logs_in(app_client_mail: MailClient) -> None:
    make_user(app_client_mail.db_path, UNVERIFIED)

    response = log_in(app_client_mail)

    assert response.status_code == 204
