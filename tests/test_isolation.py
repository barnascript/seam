"""The wall between brands is in the database, so it holds even when code forgets a filter."""
import psycopg
import pytest
from psycopg.types.json import Jsonb

from seam import db, service

from .conftest import new_style, run_jobs


def test_other_brand_cannot_see_or_open(client, brands):
    sid = new_style(client, key="key-a")
    run_jobs()
    assert client.get("/api/styles", headers={"X-API-Key": "key-b"}).json() == []
    assert client.get(f"/api/styles/{sid}", headers={"X-API-Key": "key-b"}).status_code == 404
    assert client.get(f"/api/styles/{sid}/sketch", headers={"X-API-Key": "key-b"}).status_code == 404
    assert client.get(f"/api/styles/{sid}", headers={"X-API-Key": "key-a"}).status_code == 200


def test_query_without_any_filter_still_only_sees_own_brand(client, brands):
    new_style(client, key="key-a", code="A-1")
    new_style(client, key="key-b", code="B-1")
    run_jobs()
    with db.brand_tx(brands["b"]) as conn:
        # No WHERE clause at all.
        codes = [r["code"] for r in conn.execute("select code from styles")]
        versions = conn.execute("select count(*) as n from techpack_versions").fetchone()["n"]
    assert codes == ["B-1"]
    assert versions == 1


def test_no_brand_set_means_no_rows(client, brands):
    new_style(client, key="key-a")
    with db.tx() as conn:
        assert conn.execute("select count(*) as n from styles").fetchone()["n"] == 0


def test_cannot_write_into_another_brand(client, brands):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with db.brand_tx(brands["b"]) as conn:
            conn.execute(
                "insert into styles (brand_id, code, name, garment) values (%s, 'X', 'X', 'tee')",
                [brands["a"]],
            )


def test_cannot_send_another_brands_version(client, brands):
    sid = new_style(client, key="key-a")
    run_jobs()
    v = service.get_style(brands["a"], sid)["versions"][0]
    service.approve(brands["a"], v["id"])
    r = client.post(f"/api/versions/{v['id']}/send", headers={"X-API-Key": "key-b"}, json={})
    assert r.status_code == 404


def test_wrong_key_is_rejected(client, brands):
    assert client.get("/api/styles", headers={"X-API-Key": "nope"}).status_code == 401
    assert client.get("/api/styles").status_code == 401


def test_event_log_is_walled_too(client, brands):
    sid = new_style(client, key="key-a")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with db.brand_tx(brands["b"]) as conn:
            conn.execute("insert into events (brand_id, style_id, kind, detail) values (%s, %s, 'x', %s)",
                         [brands["a"], sid, Jsonb({})])
