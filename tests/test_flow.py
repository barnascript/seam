"""Sketch to quote, end to end, through the HTTP API."""
import threading

from seam import db, service

from .conftest import new_style, run_jobs

H = {"X-API-Key": "key-a"}


def style(client, sid):
    return client.get(f"/api/styles/{sid}", headers=H).json()


def test_sketch_becomes_draft_once(client, brands):
    sid = new_style(client)
    run_jobs()
    run_jobs()
    s = style(client, sid)
    assert [(v["version"], v["status"], v["source"]) for v in s["versions"]] == [(1, "draft", "generated")]
    body = s["current"]["body"]
    assert body["features"] == [{"code": "chest_pocket", "label": "Chest pocket"}]
    assert len(body["measurements"]) == 7 and body["cost"]["target_fob_cents"] > 0


def test_duplicate_style_code_is_a_conflict(client, brands):
    new_style(client, code="DUP")
    r = client.post("/api/styles", headers=H, data={"code": "DUP", "name": "x", "garment": "tee"},
                    files={"sketch": ("t.svg", b"<svg/>", "image/svg+xml")})
    assert r.status_code == 409


def test_draft_can_never_reach_a_factory(client, brands):
    sid = new_style(client)
    run_jobs()
    v1 = style(client, sid)["versions"][0]["id"]
    r = client.post(f"/api/versions/{v1}/send", headers=H, json={})
    assert r.status_code == 409
    assert style(client, sid)["requests"] == []


def test_supplier_retries_never_double_order(client, brands):
    sid = new_style(client)
    run_jobs()
    v1 = style(client, sid)["versions"][0]["id"]
    client.post(f"/api/versions/{v1}/approve", headers=H)
    first = client.post(f"/api/versions/{v1}/send", headers=H, json={}).json()
    again = client.post(f"/api/versions/{v1}/send", headers=H, json={}).json()
    assert sorted(first["sent"]) == ["dhaka", "izmir", "porto"] and again["sent"] == []

    run_jobs()
    reqs = {r["supplier_code"]: r for r in style(client, sid)["requests"]}
    assert all(r["status"] == "quoted" for r in reqs.values())
    assert reqs["porto"]["attempts"] == 1
    assert reqs["izmir"]["attempts"] == 3   # down twice, then answered
    assert reqs["dhaka"]["attempts"] == 2   # first reply lost

    keys = "&".join(f"key={r['idempotency_key']}" for r in reqs.values())
    inbox = {r["supplier_code"]: r for r in client.get(f"/sim/suppliers/inbox?{keys}").json()}
    assert inbox["dhaka"] == {"supplier_code": "dhaka", "orders": 1, "deliveries": 2}
    assert all(r["orders"] == 1 for r in inbox.values())


def test_quotes_go_stale_only_when_the_product_changes(client, brands):
    sid = new_style(client)
    run_jobs()
    v1 = style(client, sid)["versions"][0]["id"]
    client.post(f"/api/versions/{v1}/approve", headers=H)
    client.post(f"/api/versions/{v1}/send", headers=H, json={})
    run_jobs()

    v2 = client.post(f"/api/styles/{sid}/versions", headers=H, json={"designer_note": "Bar-tack corners"}).json()
    r = client.post(f"/api/versions/{v2['id']}/approve", headers=H).json()
    assert r == {"version": 2, "replaces": 1, "quotes_stale": False}
    assert not any(x["stale"] for x in style(client, sid)["requests"])

    v3 = client.post(f"/api/styles/{sid}/versions", headers=H, json={"fabric": "linen"}).json()
    client.post(f"/api/versions/{v3['id']}/approve", headers=H)
    reqs = style(client, sid)["requests"]
    assert all(x["stale"] and x["stale_reason"] == "Main fabric" for x in reqs)

    s = style(client, sid)
    assert [v["status"] for v in s["versions"]] == ["superseded", "superseded", "approved"]


def test_only_drafts_can_be_approved(client, brands):
    sid = new_style(client)
    run_jobs()
    v1 = style(client, sid)["versions"][0]["id"]
    assert client.post(f"/api/versions/{v1}/approve", headers=H).status_code == 200
    assert client.post(f"/api/versions/{v1}/approve", headers=H).status_code == 409


def test_images_are_paid_once_and_reused(client, brands):
    sid = new_style(client)
    run_jobs()
    v1 = style(client, sid)["versions"][0]["id"]
    first = client.post(f"/api/versions/{v1}/images", headers=H, json={}).json()
    assert [x["result"] for x in first] == ["queued"] * 3
    run_jobs()
    spent = client.get("/api/me", headers=H).json()["budget"]["spent_cents"]
    assert spent == 12

    # A second style with the same look reuses the pictures for free.
    sid2 = new_style(client, code="T-2")
    run_jobs()
    v = style(client, sid2)["versions"][0]["id"]
    again = client.post(f"/api/versions/{v}/images", headers=H, json={}).json()
    assert [x["result"] for x in again] == ["reused"] * 3
    assert client.get("/api/me", headers=H).json()["budget"]["spent_cents"] == 12
    imgs = style(client, sid2)["images"]
    assert len(imgs) == 3 and all(i["from_other_style"] and i["status"] == "done" for i in imgs)
    svg = client.get(f"/api/images/{imgs[0]['id']}.svg", headers=H)
    assert svg.status_code == 200 and svg.text.startswith("<svg")


def test_budget_cannot_be_overspent_under_load(client, brands):
    with db.brand_tx(brands["a"]) as conn:
        conn.execute("update image_budgets set limit_cents = 8")  # room for two images
    ids = []
    for i, (g, fab) in enumerate([("tee", "cotton_jersey"), ("tee", "linen"), ("tee", "denim"),
                                  ("shirt", "linen"), ("dress", "linen"), ("trousers", "linen")]):
        new_style(client, code=f"B-{i}", garment=g, features="", fabric=fab)
    run_jobs()
    for s in client.get("/api/styles", headers=H).json():
        ids.append(style(client, s["id"])["versions"][0]["id"])

    results, lock = [], threading.Lock()

    def hit(vid):
        r = service.request_images(brands["a"], vid, ["flat_lay"])
        with lock:
            results.extend(x["result"] for x in r)

    ts = [threading.Thread(target=hit, args=(v,)) for v in ids]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert results.count("queued") == 2 and results.count("over_budget") == 4
    assert client.get("/api/me", headers=H).json()["budget"]["spent_cents"] == 8


def test_timeline_tells_the_story(client, brands):
    sid = new_style(client)
    run_jobs()
    v1 = style(client, sid)["versions"][0]["id"]
    client.post(f"/api/versions/{v1}/approve", headers=H)
    client.post(f"/api/versions/{v1}/send", headers=H, json={})
    run_jobs()
    t = client.get(f"/api/styles/{sid}/timeline", headers=H).json()
    kinds = [e["kind"] for e in t["events"]]
    assert kinds[:4] == ["sketch_uploaded", "draft_ready", "approved", "sent_to_suppliers"]
    assert kinds.count("quote_received") == 3 and "supplier_retry" in kinds
    assert t["sketch_to_first_quote_seconds"] is not None


def test_style_list_is_capped_and_newest_first(client, brands):
    for i in range(4):
        new_style(client, code=f"L-{i}")
    r = client.get("/api/styles?limit=3", headers=H).json()
    assert [s["code"] for s in r] == ["L-3", "L-2", "L-1"]
    assert client.get("/api/styles?limit=0", headers=H).status_code == 422
