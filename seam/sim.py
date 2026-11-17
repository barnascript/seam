"""Three pretend factory systems, so supplier handoff can be shown for real.

They behave the way factory portals really do:
- porto: answers every time.
- izmir: is down for the first two tries on each request (503).
- dhaka: saves the order, then the reply gets lost (504) the first time. If
  the sender retried without an idempotency key, the factory would now hold
  two orders. With the key it returns the same quote again.

These live in this app only because it is a demo. They keep their own table
and never read Seam's data.
"""
import hashlib
import threading
import time

from fastapi import APIRouter, Body, Header, HTTPException, Query
from psycopg.types.json import Jsonb

from . import db

router = APIRouter(prefix="/sim/suppliers", tags=["simulated suppliers"])

PROFILES = {
    "porto": {"factor": 1.12, "lead_days": 35, "moq": 300, "delay": 0.15},
    "izmir": {"factor": 0.97, "lead_days": 48, "moq": 500, "delay": 0.25},
    "dhaka": {"factor": 0.84, "lead_days": 70, "moq": 1200, "delay": 0.35},
}

_down_counts: dict[str, int] = {}
_lock = threading.Lock()


@router.post("/{code}/quote-requests")
def quote_request(code: str, body: dict = Body(...), idempotency_key: str | None = Header(default=None)):
    if code not in PROFILES:
        raise HTTPException(404, "unknown supplier")
    if not idempotency_key:
        raise HTTPException(400, "Idempotency-Key header is required")
    p = PROFILES[code]
    time.sleep(p["delay"])

    if code == "izmir":
        with _lock:
            _down_counts[idempotency_key] = _down_counts.get(idempotency_key, 0) + 1
            n = _down_counts[idempotency_key]
        if n <= 2:
            raise HTTPException(503, "supplier portal unavailable")

    fob = int(body["target_fob_cents"])
    quote = {
        "unit_price_cents": round(fob * p["factor"]),
        "currency": "EUR",
        "lead_time_days": p["lead_days"],
        "moq": p["moq"],
        "valid_days": 30,
        "reference": f"{code.upper()}-{int(hashlib.sha256(idempotency_key.encode()).hexdigest()[:8], 16) % 100000:05d}",
    }
    with db.tx() as conn:
        row = conn.execute(
            """insert into sim_supplier_inbox (idempotency_key, supplier_code, quote)
               values (%s, %s, %s)
               on conflict (idempotency_key) do update set hits = sim_supplier_inbox.hits + 1
               returning hits, quote""",
            [idempotency_key, code, Jsonb(quote)],
        ).fetchone()

    if code == "dhaka" and row["hits"] == 1:
        raise HTTPException(504, "gateway timeout (order was saved, reply lost)")
    return {"supplier": code, "quote": row["quote"], "times_seen": row["hits"]}


@router.get("/inbox")
def inbox(key: list[str] = Query(default=[])):
    """What the factories actually hold: one order per request, however many
    times it was delivered. Pass key=... to look at specific requests."""
    with db.tx() as conn:
        return conn.execute(
            """select supplier_code, count(*) as orders, sum(hits)::int as deliveries
               from sim_supplier_inbox where cardinality(%s::text[]) = 0 or idempotency_key = any(%s::text[])
               group by supplier_code order by 1""",
            [key, key],
        ).fetchall()
