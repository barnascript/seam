"""Database access.

All brand data goes through `brand_tx`, which opens a transaction and sets
app.brand_id for that transaction only. Row level security does the rest.
"""
from contextlib import contextmanager
from pathlib import Path

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from . import config

_pool: ConnectionPool | None = None


def pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            config.DATABASE_URL,
            min_size=2,
            max_size=20,
            kwargs={"row_factory": dict_row},
            open=True,
        )
    return _pool


def close() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@contextmanager
def tx():
    """A plain transaction with no brand set. RLS tables return nothing here."""
    with pool().connection() as conn:
        with conn.transaction():
            yield conn


@contextmanager
def brand_tx(brand_id):
    with pool().connection() as conn:
        with conn.transaction():
            conn.execute("select set_config('app.brand_id', %s, true)", [str(brand_id)])
            yield conn


def event(conn, brand_id, style_id, kind: str, detail: dict | None = None) -> None:
    conn.execute(
        "insert into events (brand_id, style_id, kind, detail) values (%s, %s, %s, %s)",
        [brand_id, style_id, kind, Jsonb(detail or {})],
    )


class UnsafeRole(RuntimeError):
    pass


def check_role() -> None:
    """Superusers and BYPASSRLS roles skip row level security, even forced.
    Running as one would quietly remove the wall between brands, so refuse."""
    with pool().connection() as conn:
        r = conn.execute(
            "select rolsuper, rolbypassrls from pg_roles where rolname = current_user"
        ).fetchone()
    if (r["rolsuper"] or r["rolbypassrls"]) and not config.ALLOW_RLS_BYPASS:
        raise UnsafeRole(
            "The database role skips row level security, so brands could see each other's data. "
            "Connect as a normal role (or set SEAM_ALLOW_RLS_BYPASS=1 if you really mean it)."
        )


def migrate() -> list[str]:
    applied = []
    folder = Path(__file__).resolve().parent.parent / "migrations"
    with pool().connection() as conn:
        with conn.transaction():
            # One migrator at a time, even with several app instances starting.
            conn.execute("select pg_advisory_xact_lock(4242)")
            conn.execute(
                "create table if not exists schema_migrations "
                "(name text primary key, applied_at timestamptz not null default now())"
            )
            done = {r["name"] for r in conn.execute("select name from schema_migrations")}
            for f in sorted(folder.glob("*.sql")):
                if f.name in done:
                    continue
                conn.execute(f.read_text())
                conn.execute("insert into schema_migrations (name) values (%s) on conflict do nothing", [f.name])
                applied.append(f.name)
    return applied
