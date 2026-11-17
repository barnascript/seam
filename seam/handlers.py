"""Background work. Each handler is safe to run twice: a retry after a crash
must never create a second draft, a second order or a second charge."""
import random
import time

import httpx
from psycopg.types.json import Jsonb

from . import config, db, render, sketch, techpack
from .jobs import Retry, handler
from .service import save_draft

_http = httpx.Client(timeout=httpx.Timeout(5.0, connect=2.0))


# ---------- sketch -> draft tech pack ----------

@handler("draft_techpack")
def draft_techpack(job):
    brand, p = job["brand_id"], job["payload"]
    with db.brand_tx(brand) as conn:
        style = conn.execute("select * from styles where id = %s", [p["style_id"]]).fetchone()
        sk = conn.execute("select * from sketches where id = %s", [p["sketch_id"]]).fetchone()
        done = conn.execute(
            "select 1 from techpack_versions where style_id = %s and body->'read_from'->>'sketch_id' = %s",
            [p["style_id"], p["sketch_id"]],
        ).fetchone()
    if not style or not sk or done:
        return

    # The slow call happens outside any transaction, so no locks are held while we wait.
    seen = sketch.read(sk["content_type"], bytes(sk["bytes"]), sk["hints"])
    garment = seen["garment"] or style["garment"]
    read_from = {
        "sketch_id": p["sketch_id"], "provider": seen["provider"],
        "confidence": seen["confidence"], "notes": seen["notes"],
    }
    if garment != style["garment"]:
        read_from["notes"] += f" The form said {style['garment']}; the sketch looks like {garment}."
    body = techpack.build(style["code"], style["name"], garment, style["season"],
                          sk["hints"].get("fabric"), seen["features"], read_from=read_from)

    with db.brand_tx(brand) as conn:
        # Re-check under lock: another worker may have finished first.
        conn.execute("select 1 from styles where id = %s for update", [style["id"]])
        if conn.execute(
            "select 1 from techpack_versions where style_id = %s and body->'read_from'->>'sketch_id' = %s",
            [style["id"], p["sketch_id"]],
        ).fetchone():
            return
        row = save_draft(conn, brand, style["id"], body, "generated")
        db.event(conn, brand, style["id"], "draft_ready",
                 {"version": row["version"], "read_by": seen["provider"], "confidence": seen["confidence"]})


# ---------- supplier handoff ----------

def _supplier_dead(job):
    with db.brand_tx(job["brand_id"]) as conn:
        r = conn.execute(
            "update supplier_requests set status = 'failed' where id = %s and status = 'pending' returning style_id",
            [job["payload"]["request_id"]],
        ).fetchone()
        if r:
            db.event(conn, job["brand_id"], r["style_id"], "supplier_failed", {"request_id": job["payload"]["request_id"]})


@handler("send_to_supplier", on_dead=_supplier_dead)
def send_to_supplier(job):
    brand = job["brand_id"]
    with db.brand_tx(brand) as conn:
        r = conn.execute(
            """update supplier_requests set attempts = attempts + 1 where id = %s
               returning id, style_id, status, idempotency_key, version_id, supplier_id, attempts""",
            [job["payload"]["request_id"]],
        ).fetchone()
        if not r or r["status"] != "pending":
            return
        s = conn.execute("select code, name from suppliers where id = %s", [r["supplier_id"]]).fetchone()
        v = conn.execute("select version, body from techpack_versions where id = %s", [r["version_id"]]).fetchone()

    body = v["body"]
    payload = {
        "style_code": body["header"]["style_code"],
        "version": v["version"],
        "garment": body["header"]["garment_label"],
        "fabric": body["header"]["fabric"]["label"],
        "sizes": body["header"]["sizes"],
        "measurements": body["measurements"],
        "bom": body["bom"],
        "construction": body["construction"],
        "target_fob_cents": body["cost"]["target_fob_cents"],
    }
    try:
        resp = _http.post(f"{config.SUPPLIER_BASE_URL}/{s['code']}/quote-requests",
                          json=payload, headers={"Idempotency-Key": r["idempotency_key"]})
    except httpx.HTTPError as e:
        error = f"{type(e).__name__}: {e}"
    else:
        if resp.status_code == 200:
            quote = resp.json()["quote"]
            with db.brand_tx(brand) as conn:
                done = conn.execute(
                    """update supplier_requests set status = 'quoted', quote = %s, quoted_at = now(), last_error = null
                       where id = %s and status = 'pending' returning id""",
                    [Jsonb(quote), r["id"]],
                ).fetchone()
                if done:
                    db.event(conn, brand, r["style_id"], "quote_received",
                             {"supplier": s["code"], "attempt": r["attempts"], "unit_price_cents": quote["unit_price_cents"]})
            return
        if 400 <= resp.status_code < 500 and resp.status_code not in (408, 429):
            # The supplier rejected the request itself. Retrying won't help.
            raise RuntimeError(f"supplier rejected request: {resp.status_code} {resp.text[:200]}")
        error = f"HTTP {resp.status_code}: {resp.text[:200]}"

    with db.brand_tx(brand) as conn:
        conn.execute("update supplier_requests set last_error = %s where id = %s", [error, r["id"]])
        db.event(conn, brand, r["style_id"], "supplier_retry", {"supplier": s["code"], "attempt": r["attempts"], "error": error})
    raise Retry(error)


# ---------- images ----------

def _image_dead(job):
    with db.brand_tx(job["brand_id"]) as conn:
        img = conn.execute(
            "update images set status = 'failed' where id = %s and status = 'queued' returning style_id, cost_cents",
            [job["payload"]["image_id"]],
        ).fetchone()
        if img:
            conn.execute("update image_budgets set spent_cents = spent_cents - %s", [img["cost_cents"]])
            db.event(conn, job["brand_id"], img["style_id"], "image_failed", {"refunded_cents": img["cost_cents"]})


@handler("render_image", on_dead=_image_dead)
def render_image(job):
    brand = job["brand_id"]
    with db.brand_tx(brand) as conn:
        img = conn.execute("select * from images where id = %s", [job["payload"]["image_id"]]).fetchone()
        if not img or img["status"] != "queued":
            return
        v = conn.execute("select body from techpack_versions where id = %s", [img["version_id"]]).fetchone()

    body = v["body"]
    seed = img["provenance"]["seed"]
    time.sleep(random.uniform(0.4, 1.2))  # stands in for model time
    svg = render.render(body["header"]["garment"], body["header"]["fabric"]["colour"],
                        [f["code"] for f in body["features"]], img["shot"], seed)

    with db.brand_tx(brand) as conn:
        done = conn.execute(
            """update images set status = 'done', svg = %s, finished_at = now(),
                      provenance = provenance || %s
               where id = %s and status = 'queued' returning id""",
            [svg, Jsonb({"rendered_by_job": job["id"], "attempt": job["attempts"]}), img["id"]],
        ).fetchone()
        if done:
            db.event(conn, brand, img["style_id"], "image_ready", {"shot": img["shot"]})
