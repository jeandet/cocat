import re
from pathlib import Path

import pytest

from cocat import DB
from cocat.votable import (
    export_votable_file,
    export_votable_str,
    import_votable_file,
    import_votable_str,
)

HERE = Path(__file__).parent


def test_export_different_attributes():
    db = DB()

    event0 = db.create_event(
        start="2025-01-31",
        stop="2026-01-31",
        author="Paul",
        attributes={
            "key0": 0,
            "key1": 1,
        },
    )
    event1 = db.create_event(
        start="2027-01-31",
        stop="2028-01-31",
        author="Mike",
        attributes={
            "key1": 2,
            "key2": 3,
        },
    )
    catalogue = db.create_catalogue(
        name="cat",
        author="John",
        attributes={
            "key3": 4,
            "key4": 5,
        },
    )
    catalogue.add_events([event0, event1])

    with pytest.raises(
        ValueError,
        match=re.escape(
            "Export VOTable: not all attributes are present in all events ('key0', 'key2')"
        ),
    ):
        export_votable_str(catalogue)


def test_export_different_attribute_types():
    db = DB()

    event0 = db.create_event(
        start="2025-01-31",
        stop="2026-01-31",
        author="Paul",
        attributes={
            "key0": 0,
            "key1": 1,
        },
    )
    event1 = db.create_event(
        start="2027-01-31",
        stop="2028-01-31",
        author="Mike",
        attributes={
            "key0": "foo",
            "key1": "bar",
        },
    )
    catalogue = db.create_catalogue(
        name="cat",
        author="John",
        attributes={
            "key3": 4,
            "key4": 5,
        },
    )
    catalogue.add_events([event0, event1])

    with pytest.raises(
        ValueError,
        match=re.escape(
            "Export VOTable: not all value types are identical for all events for attribute key0"
        ),
    ):
        export_votable_str(catalogue)


def test_export_import(tmp_path):
    votable_path = tmp_path / "votable.xml"
    db = DB()

    event0 = db.create_event(
        start="2025-01-31",
        stop="2026-01-31",
        author="Paul",
        attributes={
            "key0": 0,
            "key1": 1,
        },
    )
    event1 = db.create_event(
        start="2027-01-31",
        stop="2028-01-31",
        author="Mike",
        attributes={
            "key0": 2,
            "key1": 3,
        },
    )
    catalogue = db.create_catalogue(
        name="cat",
        author="John",
        attributes={
            "key3": 4,
            "key4": 5,
        },
    )
    catalogue.add_events([event0, event1])

    export_votable_file(catalogue, votable_path)

    db = DB()
    import_votable_file(votable_path, db)

    assert db.events == {event0, event1}
    assert len(db.catalogues) == 1
    assert next(iter(db.catalogues)).name == catalogue.name

    votable_str = export_votable_str(catalogue)

    db = DB()
    import_votable_str(votable_str, db)

    assert db.events == {event0, event1}
    assert len(db.catalogues) == 1
    assert next(iter(db.catalogues)).name == catalogue.name


def test_import_file():
    table = HERE / "data" / "Dst_Li2020.xml"
    db = DB()
    import_votable_file(table, db)

    assert len(db.catalogues) == 1
    catalogue = next(iter(db.catalogues))
    assert catalogue.name == "Dst_Li2020"
    assert len(db.events) == 95
    assert len(catalogue.events) == 95
    assert (
        len(
            [
                event
                for event in catalogue.events
                if event.author == "vincent.genot@irap.omp.eu"
            ]
        )
        == 95
    )


def test_export_an_empty_catalogue():
    db = DB()
    catalogue = db.create_catalogue(name="empty", author="John")

    xml = export_votable_str(catalogue)

    assert "<TABLE" in xml
    imported = DB()
    import_votable_str(xml, imported)
    assert imported.events == set()


def test_export_without_a_catalogue():
    xml = export_votable_str([])

    assert "<TABLE" not in xml

    imported = DB()
    import_votable_str(xml, imported)
    assert imported.events == set()


def test_each_catalogue_keeps_its_own_attributes():
    db = DB()
    first = db.create_catalogue(name="first", author="John")
    first.add_events(
        [db.create_event(start=0, stop=1, author="John", attributes={"foo": 1.0})]
    )
    second = db.create_catalogue(name="second", author="John")
    second.add_events(
        [db.create_event(start=0, stop=1, author="John", attributes={"bar": 2.0})]
    )

    xml = export_votable_str([first, second])

    imported = DB()
    import_votable_str(xml, imported)
    assert sorted(
        (
            event.attributes
            for catalogue in imported.catalogues
            for event in catalogue.events
        ),
        key=lambda attributes: sorted(attributes),
    ) == [{"bar": 2.0}, {"foo": 1.0}]
