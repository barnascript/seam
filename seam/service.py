"""What the API can do. Each function runs inside one brand's transaction."""
import hashlib
import json

import psycopg
from psycopg.types.json import Jsonb

from . import config, db, jobs, techpack

SHOTS = {"flat_lay": "Flat lay", "on_model": "On model", "detail": "Detail close-up"}
IMAGE_MODEL = "seam-preview-1"


class NotFound(Exception):
    pass


class Conflict(Exception):
    pass


class Invalid(Exception):
    pass


# ---------- styles ----------

def create_style(brand_id, *, code, name, garment, season, fabric, features, content_type, data):
    if garment not in techpack.GARMENTS:
        raise Invalid(f"unknown garment {garment!r}")
    if fabric and fabric not in techpack.FABRICS:
        raise Invalid(f"unknown fabric {fabric!r}")
    if len(data) > config.MAX_SKETCH_BYTES:
        raise Invalid("sketch is over 2 MB")
    try:
        with db.brand_tx(brand_id) as conn:
            style = conn.execute(
                "insert into styles (brand_id, code, name, garment, season) values (%s, %s, %s, %s, %s) returning id",
                [brand_id, code, name, garment, season],
            ).fetchone()
            sketch = conn.execute(
                """insert into sketches (brand_id, style_id, content_type, bytes, sha256, hints)
                   values (%s, %s, %s, %s, %s, %s) returning id""",
                [brand_id, style["id"], content_type, data, hashlib.sha256(data).hexdigest(),
                 Jsonb({"garment": garment, "fabric": fabric, "features": features})],
            ).fetchone()
            db.event(conn, brand_id, style["id"], "sketch_uploaded", {"bytes": len(data), "type": content_type})
            # Same transaction as the style: either both exist or neither does.
            jobs.enqueue(conn, brand_id, "draft_techpack",
                         {"style_id": str(style["id"]), "sketch_id": str(sketch["id"])},
                         dedupe_key=str(sketch["id"]), priority=10)
    except psycopg.errors.UniqueViolation:
        raise Conflict(f"style code {code!r} already exists")
    return style["id"]


def list_styles(brand_id, limit: int = 50):
    with db.brand_tx(brand_id) as conn:
        return conn.execute(
            """select s.id, s.code, s.name, s.garment, s.season, s.created_at,
                      v.version as latest_version, v.status as latest_status,
                      (select count(*) from supplier_requests r where r.style_id = s.id and r.status = 'quoted') as quotes
               from styles s
               left join lateral (select version, status from techpack_versions
                                  where style_id = s.id order by version desc limit 1) v on true
               order by s.created_at desc
               limit %s""",
            [limit],
        ).fetchall()


def _style(conn, style_id):
    s = conn.execute("select * from styles where id = %s", [style_id]).fetchone()
    if not s:
        raise NotFound("style not found")
    return s


def _versions(conn, style_id):
    return conn.execute(
        "select * from techpack_versions where style_id = %s order by version", [style_id]
    ).fetchall()


def get_style(brand_id, style_id):
    with db.brand_tx(brand_id) as conn:
        style = _style(conn, style_id)
        versions = _versions(conn, style_id)
        approved = next((v for v in versions if v["status"] == "approved"), None)
        requests = conn.execute(
            """select r.*, s.code as supplier_code, s.name as supplier_name, s.country, v.version
               from supplier_requests r join suppliers s on s.id = r.supplier_id
               join techpack_versions v on v.id = r.version_id
               where r.style_id = %s order by v.version, s.code""",
            [style_id],
        ).fetchall()
        images = conn.execute(
            """select * from (
                   select distinct on (i.id)
                          i.id, i.shot, i.status, i.cost_cents, i.reused, i.provenance, i.created_at, i.finished_at,
                          v.version, (i.style_id <> u.style_id) as from_other_style
                   from image_uses u
                   join images i on i.id = u.image_id
                   join techpack_versions v on v.id = u.version_id
                   where u.style_id = %s
                   order by i.id, v.version desc) x
               order by created_at""",
            [style_id],
        ).fetchall()
        budget = conn.execute("select limit_cents, spent_cents from image_budgets").fetchone()

    by_id = {v["id"]: v for v in versions}
    for r in requests:
        r["stale"] = False
        r["stale_reason"] = None
        if approved and r["version_id"] != approved["id"]:
            d = techpack.diff(by_id[r["version_id"]]["body"], approved["body"])
            if d["affects_quote"]:
                r["stale"] = True
                r["stale_reason"] = ", ".join(c["label"] for c in d["changes"] if c["affects_quote"])
    return {
        "style": style,
        "versions": [{k: v[k] for k in ("id", "version", "status", "source", "note", "created_at", "approved_at")}
                     for v in versions],
        "current": (approved or (versions[-1] if versions else None)),
        "requests": requests,
        "images": images,
        "budget": budget,
    }


def get_version(brand_id, style_id, n: int):
    with db.brand_tx(brand_id) as conn:
        v = conn.execute(
            "select * from techpack_versions where style_id = %s and version = %s", [style_id, n]
        ).fetchone()
    if not v:
        raise NotFound("version not found")
    return v


def diff_versions(brand_id, style_id, a: int, b: int):
    va, vb = get_version(brand_id, style_id, a), get_version(brand_id, style_id, b)
    return {"from": a, "to": b, **techpack.diff(va["body"], vb["body"])}


def sketch_of(brand_id, style_id):
    with db.brand_tx(brand_id) as conn:
        row = conn.execute(
            "select content_type, bytes from sketches where style_id = %s order by created_at desc limit 1",
            [style_id],
        ).fetchone()
    if not row:
        raise NotFound("no sketch")
    return row["content_type"], bytes(row["bytes"])


# ---------- versions ----------

def save_draft(conn, brand_id, style_id, body, source, parent_id=None, note=None):
    n = conn.execute(
        "select coalesce(max(version), 0) + 1 as n from techpack_versions where style_id = %s", [style_id]
    ).fetchone()["n"]
    return conn.execute(
        """insert into techpack_versions (brand_id, style_id, version, status, source, parent_id, body, note)
           values (%s, %s, %s, 'draft', %s, %s, %s, %s) returning id, version""",
        [brand_id, style_id, n, source, parent_id, Jsonb(body), note],
    ).fetchone()


def edit(brand_id, style_id, change: dict):
    fabric = change.get("fabric")
    if fabric and fabric not in techpack.FABRICS:
        raise Invalid(f"unknown fabric {fabric!r}")
    with db.brand_tx(brand_id) as conn:
        _style(conn, style_id)
        # Lock the style's latest version so two edits in a race get versions n+1 and n+2, not two n+1s.
        latest = conn.execute(
            "select * from techpack_versions where style_id = %s order by version desc limit 1 for update",
            [style_id],
        ).fetchone()
        if not latest:
            raise Conflict("the first draft is not ready yet")
        try:
            body = techpack.apply_edit(latest["body"], change)
        except (ValueError, KeyError) as e:
            raise Invalid(str(e))
        d = techpack.diff(latest["body"], body)
        if not d["changes"]:
            raise Invalid("nothing changed")
        row = save_draft(conn, brand_id, style_id, body, "edited", latest["id"], change.get("designer_note"))
        db.event(conn, brand_id, style_id, "edited",
                 {"version": row["version"], "changes": [c["label"] for c in d["changes"]]})
    return row


def approve(brand_id, version_id):
    with db.brand_tx(brand_id) as conn:
        v = conn.execute("select * from techpack_versions where id = %s for update", [version_id]).fetchone()
        if not v:
            raise NotFound("version not found")
        if v["status"] != "draft":
            raise Conflict(f"version {v['version']} is {v['status']}, only drafts can be approved")
        prev = conn.execute(
            "update techpack_versions set status = 'superseded' where style_id = %s and status = 'approved' returning *",
            [v["style_id"]],
        ).fetchone()
        conn.execute("update techpack_versions set status = 'approved', approved_at = now() where id = %s", [version_id])
        detail = {"version": v["version"]}
        if prev:
            d = techpack.diff(prev["body"], v["body"])
            detail["replaces"] = prev["version"]
            detail["quotes_stale"] = d["affects_quote"]
        db.event(conn, brand_id, v["style_id"], "approved", detail)
    return detail


# ---------- suppliers ----------

def suppliers(brand_id):
    with db.brand_tx(brand_id) as conn:
        return conn.execute("select id, code, name, country from suppliers order by code").fetchall()


def send_to_suppliers(brand_id, version_id, codes: list[str] | None):
    with db.brand_tx(brand_id) as conn:
        v = conn.execute("select id, style_id, version from techpack_versions where id = %s", [version_id]).fetchone()
        if not v:
            raise NotFound("version not found")
        sql = "select * from suppliers" + (" where code = any(%s)" if codes else "") + " order by code"
        targets = conn.execute(sql, [codes] if codes else []).fetchall()
        if not targets:
            raise Invalid("no matching suppliers")
        created = []
        try:
            for s in targets:
                r = conn.execute(
                    """insert into supplier_requests (brand_id, style_id, version_id, supplier_id, idempotency_key)
                       values (%s, %s, %s, %s, %s)
                       on conflict (version_id, supplier_id) do nothing returning id""",
                    [brand_id, v["style_id"], version_id, s["id"], f"seam:{version_id}:{s['code']}"],
                ).fetchone()
                if r:
                    created.append(s["code"])
                    jobs.enqueue(conn, brand_id, "send_to_supplier", {"request_id": str(r["id"])},
                                 dedupe_key=str(r["id"]), priority=20, max_attempts=6)
        except psycopg.errors.CheckViolation:
            raise Conflict("only an approved tech pack can go to suppliers")
        if created:
            db.event(conn, brand_id, v["style_id"], "sent_to_suppliers", {"version": v["version"], "suppliers": created})
    return {"sent": created, "already_sent": [s["code"] for s in targets if s["code"] not in created]}


# ---------- images ----------

def image_hash(body: dict, shot: str) -> str:
    # The picture depends on what the garment looks like, not on its version
    # number. A note or a measurement tweak keeps the same look, so the image
    # we already paid for is reused.
    look = {
        "garment": body["header"]["garment"],
        "fabric": body["header"]["fabric"]["code"],
        "features": sorted(f["code"] for f in body["features"]),
        "shot": shot,
        "model": IMAGE_MODEL,
    }
    return hashlib.sha256(json.dumps(look, sort_keys=True).encode()).hexdigest()


def request_images(brand_id, version_id, shots: list[str]):
    bad = [s for s in shots if s not in SHOTS]
    if bad or not shots:
        raise Invalid(f"shots must be from {list(SHOTS)}")
    results = []
    with db.brand_tx(brand_id) as conn:
        v = conn.execute("select id, style_id, version, body from techpack_versions where id = %s", [version_id]).fetchone()
        if not v:
            raise NotFound("version not found")
        for shot in dict.fromkeys(shots):
            h = image_hash(v["body"], shot)
            with conn.transaction():  # savepoint per shot
                row = conn.execute(
                    """insert into images (brand_id, style_id, version_id, request_hash, shot, cost_cents, provenance)
                       values (%s, %s, %s, %s, %s, %s, %s)
                       on conflict (brand_id, request_hash) do nothing returning id""",
                    [brand_id, v["style_id"], version_id, h, shot, config.IMAGE_COST_CENTS,
                     Jsonb({"model": IMAGE_MODEL, "seed": int(h[:8], 16), "request_hash": h,
                            "from_version": v["version"], "inputs": "tech pack only, no third-party images"})],
                ).fetchone()
                if not row:
                    existing = conn.execute(
                        "select id from images where request_hash = %s", [h]
                    ).fetchone()
                    used = conn.execute(
                        """insert into image_uses (brand_id, image_id, style_id, version_id)
                           values (%s, %s, %s, %s) on conflict do nothing returning image_id""",
                        [brand_id, existing["id"], v["style_id"], version_id],
                    ).fetchone()
                    if used:
                        conn.execute("update images set reused = reused + 1 where id = %s", [existing["id"]])
                    results.append({"shot": shot, "result": "reused", "image_id": existing["id"], "cost_cents": 0})
                    continue
                # One UPDATE checks and spends the budget. Two requests racing
                # for the last few cents can't both win.
                paid = conn.execute(
                    """update image_budgets set spent_cents = spent_cents + %s
                       where spent_cents + %s <= limit_cents returning spent_cents""",
                    [config.IMAGE_COST_CENTS, config.IMAGE_COST_CENTS],
                ).fetchone()
                if not paid:
                    conn.execute("delete from images where id = %s", [row["id"]])
                    results.append({"shot": shot, "result": "over_budget", "cost_cents": 0})
                    continue
                conn.execute(
                    "insert into image_uses (brand_id, image_id, style_id, version_id) values (%s, %s, %s, %s)",
                    [brand_id, row["id"], v["style_id"], version_id],
                )
                jobs.enqueue(conn, brand_id, "render_image", {"image_id": str(row["id"])},
                             dedupe_key=str(row["id"]), priority=50, max_attempts=4)
                results.append({"shot": shot, "result": "queued", "image_id": row["id"],
                                "cost_cents": config.IMAGE_COST_CENTS})
        if any(r["result"] != "over_budget" for r in results):
            db.event(conn, brand_id, v["style_id"], "images_requested",
                     {"version": v["version"], "shots": {r["shot"]: r["result"] for r in results}})
    return results


def image_svg(brand_id, image_id):
    with db.brand_tx(brand_id) as conn:
        row = conn.execute("select svg, status from images where id = %s", [image_id]).fetchone()
    if not row or row["status"] != "done":
        raise NotFound("image not ready")
    return row["svg"]


def budget(brand_id):
    with db.brand_tx(brand_id) as conn:
        return conn.execute("select limit_cents, spent_cents from image_budgets").fetchone()


# ---------- timeline ----------

LABELS = {
    "sketch_uploaded": "Sketch uploaded",
    "draft_ready": "Draft tech pack ready",
    "edited": "Designer edited",
    "approved": "Tech pack approved",
    "sent_to_suppliers": "Sent to suppliers",
    "supplier_retry": "Supplier did not answer, retrying",
    "quote_received": "Quote received",
    "supplier_failed": "Supplier gave up",
    "images_requested": "Images requested",
    "image_ready": "Image ready",
    "image_failed": "Image failed, budget refunded",
}


def timeline(brand_id, style_id):
    with db.brand_tx(brand_id) as conn:
        _style(conn, style_id)
        rows = conn.execute(
            "select kind, detail, at from events where style_id = %s order by id", [style_id]
        ).fetchall()
    if not rows:
        return {"events": [], "sketch_to_first_quote_seconds": None}
    start = rows[0]["at"]
    first_quote = next((r["at"] for r in rows if r["kind"] == "quote_received"), None)
    return {
        "events": [{"kind": r["kind"], "label": LABELS.get(r["kind"], r["kind"]), "detail": r["detail"],
                    "at": r["at"], "seconds": round((r["at"] - start).total_seconds(), 2)} for r in rows],
        "sketch_to_first_quote_seconds": round((first_quote - start).total_seconds(), 1) if first_quote else None,
    }
