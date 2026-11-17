"""A small job queue on Postgres.

Why not Redis or a broker: the job is written in the same transaction as the
data that needs it. If the request fails, the job never existed. If the job is
written, the data is there too. No dual-write gap.

- Workers claim with FOR UPDATE SKIP LOCKED, so many workers never grab the
  same job and never wait on each other.
- A claim is a lease. If a worker dies mid-job, the lease runs out and another
  worker picks the job up again.
- Failures back off (1s, 2s, 4s, ...). After max_attempts the job is "dead"
  and kept for a person to look at.
"""
import logging
import os
import socket
import threading
import time
import traceback

from psycopg.types.json import Jsonb

from . import db

log = logging.getLogger("seam.jobs")

LEASE_SECONDS = 30
HANDLERS: dict = {}
ON_DEAD: dict = {}


class Retry(Exception):
    """Raise from a handler to retry without logging a stack trace."""


def handler(kind: str, on_dead=None):
    def wrap(fn):
        HANDLERS[kind] = fn
        if on_dead:
            ON_DEAD[kind] = on_dead
        return fn
    return wrap


def enqueue(conn, brand_id, kind: str, payload: dict, dedupe_key: str | None = None,
            priority: int = 100, max_attempts: int = 6) -> int | None:
    """Add a job inside the caller's transaction. Returns None if an identical
    job is already waiting or running."""
    row = conn.execute(
        """insert into jobs (brand_id, kind, payload, dedupe_key, priority, max_attempts)
           values (%s, %s, %s, %s, %s, %s)
           on conflict (kind, dedupe_key) where dedupe_key is not null and status in ('queued', 'running')
           do nothing
           returning id""",
        [brand_id, kind, Jsonb(payload), dedupe_key, priority, max_attempts],
    ).fetchone()
    return row["id"] if row else None


def claim(worker_id: str):
    with db.tx() as conn:
        return conn.execute(
            """update jobs set status = 'running',
                               attempts = attempts + 1,
                               locked_until = now() + make_interval(secs => %s),
                               locked_by = %s
               where id = (
                   select id from jobs
                   where (status = 'queued' and run_after <= now())
                      or (status = 'running' and locked_until < now())
                   order by priority, run_after
                   for update skip locked
                   limit 1)
               returning *""",
            [LEASE_SECONDS, worker_id],
        ).fetchone()


def complete(job) -> bool:
    with db.tx() as conn:
        # Only the worker holding the lease may close the job.
        row = conn.execute(
            """update jobs set status = 'done', finished_at = now(), locked_until = null
               where id = %s and locked_by = %s and status = 'running' returning id""",
            [job["id"], job["locked_by"]],
        ).fetchone()
    return row is not None


def fail(job, error: str) -> str:
    dead = job["attempts"] >= job["max_attempts"]
    backoff = min(2 ** (job["attempts"] - 1), 60)
    with db.tx() as conn:
        conn.execute(
            """update jobs set status = %s, last_error = %s, locked_until = null,
                               run_after = now() + make_interval(secs => %s),
                               finished_at = case when %s then now() end
               where id = %s and locked_by = %s""",
            ["dead" if dead else "queued", error[:2000], backoff, dead, job["id"], job["locked_by"]],
        )
    if dead and job["kind"] in ON_DEAD:
        try:
            ON_DEAD[job["kind"]](job)
        except Exception:
            log.exception("on_dead hook failed for job %s", job["id"])
    return "dead" if dead else "queued"


def run_one(worker_id: str) -> bool:
    job = claim(worker_id)
    if not job:
        return False
    fn = HANDLERS.get(job["kind"])
    try:
        if fn is None:
            raise RuntimeError(f"no handler for {job['kind']}")
        fn(job)
    except Retry as e:
        fail(job, str(e))
    except Exception as e:
        log.warning("job %s (%s) attempt %s failed: %s", job["id"], job["kind"], job["attempts"], e)
        fail(job, f"{e}\n{traceback.format_exc(limit=3)}")
    else:
        complete(job)
    return True


def drain(worker_id: str = "drain", limit: int = 1000) -> int:
    """Run ready jobs until none are left. Used by tests."""
    n = 0
    while n < limit and run_one(worker_id):
        n += 1
    return n


class Worker:
    def __init__(self, threads: int = 4):
        self.threads = threads
        self._stop = threading.Event()
        self._pool: list[threading.Thread] = []

    def start(self):
        base = f"{socket.gethostname()}:{os.getpid()}"
        for i in range(self.threads):
            t = threading.Thread(target=self._loop, args=(f"{base}:{i}",), daemon=True, name=f"seam-worker-{i}")
            t.start()
            self._pool.append(t)

    def stop(self, timeout: float = 5.0):
        self._stop.set()
        for t in self._pool:
            t.join(timeout)

    def _loop(self, worker_id: str):
        idle = 0.05
        while not self._stop.is_set():
            try:
                worked = run_one(worker_id)
            except Exception:
                log.exception("worker loop error")
                worked = False
            if worked:
                idle = 0.05
            else:
                self._stop.wait(idle)
                idle = min(idle * 2, 0.5)


def stats(brand_id) -> dict:
    with db.tx() as conn:
        rows = conn.execute(
            "select kind, status, count(*) as n from jobs where brand_id = %s group by kind, status",
            [brand_id],
        ).fetchall()
    out: dict = {}
    for r in rows:
        out.setdefault(r["kind"], {})[r["status"]] = r["n"]
    return out


if __name__ == "__main__":
    # Run as its own process: python -m seam.jobs
    logging.basicConfig(level=logging.INFO)
    from . import handlers  # noqa: F401  registers handlers
    w = Worker(int(os.environ.get("WORKER_THREADS", "4")))
    w.start()
    while True:
        time.sleep(3600)
