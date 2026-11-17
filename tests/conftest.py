import os

os.environ.setdefault("DATABASE_URL", "postgresql://seam_owner:seam@127.0.0.1:55432/seamtest")
os.environ["RUN_WORKER"] = "0"
os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["SUPPLIER_BASE_URL"] = "http://testserver/sim/suppliers"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from seam import api, db, handlers, jobs, seed, sim  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db():
    with db.tx() as conn:
        conn.execute("drop schema public cascade")
        conn.execute("create schema public")
    db.check_role()
    db.migrate()
    sim._down_counts.clear()
    yield


@pytest.fixture
def client():
    c = TestClient(api.app)  # no `with`, so the app's own worker never starts
    handlers._http = c        # supplier calls go to the in-app test factories
    return c


@pytest.fixture
def brands():
    a = seed.ensure_brand("Brand A", "key-a")
    b = seed.ensure_brand("Brand B", "key-b")
    return {"a": a, "b": b}


def run_jobs(rounds: int = 20) -> None:
    """Run every job, skipping retry back-off so tests stay fast."""
    for _ in range(rounds):
        with db.tx() as conn:
            conn.execute("update jobs set run_after = now() where status = 'queued'")
        if jobs.drain("test") == 0:
            return


SKETCH = b'<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0 L10 10"/></svg>'


def new_style(client, key="key-a", code="T-1", garment="tee", features="chest_pocket", fabric=""):
    r = client.post(
        "/api/styles",
        headers={"X-API-Key": key},
        data={"code": code, "name": "Test tee", "garment": garment, "season": "SS27",
              "features": features, "fabric": fabric},
        files={"sketch": ("t.svg", SKETCH, "image/svg+xml")},
    )
    assert r.status_code == 202, r.text
    return r.json()["id"]
