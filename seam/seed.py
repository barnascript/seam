"""Two made-up brands for the demo. Safe to run on every start."""
import hashlib

from . import config, db

DEMO_BRANDS = [
    {"name": "Northwind Apparel", "key": "demo-northwind"},
    {"name": "Kestrel Studio", "key": "demo-kestrel"},
]

SUPPLIERS = [
    ("porto", "Porto test factory", "Portugal"),
    ("izmir", "Izmir test factory", "Türkiye"),
    ("dhaka", "Dhaka test factory", "Bangladesh"),
]


def key_hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def ensure_brand(name: str, key: str, budget_cents: int | None = None):
    with db.tx() as conn:
        b = conn.execute(
            """insert into brands (name, api_key_hash) values (%s, %s)
               on conflict (api_key_hash) do update set name = excluded.name returning id""",
            [name, key_hash(key)],
        ).fetchone()
    with db.brand_tx(b["id"]) as conn:
        conn.execute(
            "insert into image_budgets (brand_id, limit_cents) values (%s, %s) on conflict do nothing",
            [b["id"], budget_cents if budget_cents is not None else config.DEMO_IMAGE_BUDGET_CENTS],
        )
        for code, sname, country in SUPPLIERS:
            conn.execute(
                "insert into suppliers (brand_id, code, name, country) values (%s, %s, %s, %s) on conflict do nothing",
                [b["id"], code, sname, country],
            )
    return b["id"]


def ensure_demo():
    return [ensure_brand(b["name"], b["key"]) for b in DEMO_BRANDS]


def brand_for_key(key: str):
    with db.tx() as conn:
        return conn.execute("select id, name from brands where api_key_hash = %s", [key_hash(key)]).fetchone()
