"""The job queue: written with the data, safe across crashes, never doubled."""
import threading

import pytest

from seam import db, jobs

RAN = []


@jobs.handler("test_ok")
def _ok(job):
    RAN.append(job["id"])


@jobs.handler("test_boom")
def _boom(job):
    raise RuntimeError("boom")


@pytest.fixture(autouse=True)
def _clear():
    RAN.clear()


def add(brand, kind="test_ok", key=None, max_attempts=6):
    with db.tx() as conn:
        return jobs.enqueue(conn, brand, kind, {}, dedupe_key=key, max_attempts=max_attempts)


def test_job_rolls_back_with_its_data(brands):
    with pytest.raises(RuntimeError):
        with db.tx() as conn:
            jobs.enqueue(conn, brands["a"], "test_ok", {})
            raise RuntimeError("request failed after enqueue")
    assert jobs.drain() == 0


def test_dedupe_while_waiting(brands):
    assert add(brands["a"], key="same") is not None
    assert add(brands["a"], key="same") is None
    jobs.drain()
    # Once done, the same key may run again.
    assert add(brands["a"], key="same") is not None


def test_crashed_worker_job_is_picked_up_again(brands):
    jid = add(brands["a"])
    job = jobs.claim("worker-1")
    assert job["id"] == jid
    # worker-1 dies here. Its lease runs out.
    with db.tx() as conn:
        conn.execute("update jobs set locked_until = now() - interval '1 second' where id = %s", [jid])
    again = jobs.claim("worker-2")
    assert again["id"] == jid and again["attempts"] == 2
    # worker-1 comes back from the dead and tries to finish: it lost the lease, so it can't.
    assert jobs.complete(job) is False
    assert jobs.complete(again) is True


def test_no_two_workers_get_the_same_job(brands):
    for _ in range(40):
        add(brands["a"])
    seen, lock = [], threading.Lock()

    def worker(n):
        while True:
            j = jobs.claim(f"w{n}")
            if not j:
                return
            with lock:
                seen.append(j["id"])
            jobs.complete(j)

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(seen) == 40 and len(set(seen)) == 40


def test_failures_back_off_then_go_dead(brands):
    jid = add(brands["a"], kind="test_boom", max_attempts=3)
    for expected in ("queued", "queued", "dead"):
        with db.tx() as conn:
            conn.execute("update jobs set run_after = now() where id = %s", [jid])
        assert jobs.run_one("w") is True
        with db.tx() as conn:
            row = conn.execute("select status, run_after > now() as later from jobs where id = %s", [jid]).fetchone()
        assert row["status"] == expected
        if expected == "queued":
            assert row["later"]  # waits before the next try
    assert jobs.run_one("w") is False  # dead jobs stay put for a person to look at
