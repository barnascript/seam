"""Load test against a running Seam.

    python scripts/loadtest.py http://localhost:8000 --styles 300 --concurrency 32

1. Uploads N sketches at once and times each upload.
2. Waits for the queue to draft every tech pack, and times that.
3. Approves each one and sends it to all three factories (a third of them flaky).
4. Hammers the read endpoints.
Prints p50 / p95 / p99 for each step.
"""
import argparse
import statistics
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

SKETCH = (Path(__file__).resolve().parent.parent / "static" / "samples" / "tee.svg").read_bytes()


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * p / 100))] * 1000


def report(name, xs, wall):
    print(f"{name:<22} n={len(xs):<5} p50={pct(xs, 50):6.1f}ms  p95={pct(xs, 95):6.1f}ms  "
          f"p99={pct(xs, 99):6.1f}ms  {len(xs) / wall:7.1f} req/s")


RECONNECTS = 0


def timed(fn):
    """Time one request. A dropped idle connection is retried once and counted."""
    global RECONNECTS
    t = time.perf_counter()
    try:
        r = fn()
    except (httpx.RemoteProtocolError, httpx.ConnectError):
        RECONNECTS += 1
        r = fn()
    r.raise_for_status()
    return time.perf_counter() - t, r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url", nargs="?", default="http://localhost:8000")
    ap.add_argument("--styles", type=int, default=200)
    ap.add_argument("--concurrency", type=int, default=32)
    ap.add_argument("--key", default="demo-kestrel")
    a = ap.parse_args()

    h = {"X-API-Key": a.key}
    run = uuid.uuid4().hex[:6].upper()
    client = httpx.Client(base_url=a.url, headers=h, timeout=30,
                          limits=httpx.Limits(max_connections=a.concurrency))
    pool = ThreadPoolExecutor(a.concurrency)

    def upload(i):
        return timed(lambda: client.post("/api/styles", data={
            "code": f"LT-{run}-{i}", "name": f"Load tee {i}", "garment": "tee", "features": "chest_pocket"},
            files={"sketch": ("t.svg", SKETCH, "image/svg+xml")}))

    t0 = time.perf_counter()
    res = list(pool.map(upload, range(a.styles)))
    report("upload sketch", [r[0] for r in res], time.perf_counter() - t0)
    ids = [r[1].json()["id"] for r in res]

    t0 = time.perf_counter()
    while True:
        styles = timed(lambda: client.get("/api/styles", params={"limit": 200}))[1].json()
        mine = [s for s in styles if s["code"].startswith(f"LT-{run}-")]
        if len(mine) >= min(a.styles, 200) and all(s["latest_version"] for s in mine):
            break
        if time.perf_counter() - t0 > 300:
            raise SystemExit("queue did not finish drafting in 5 minutes")
        time.sleep(0.2)
    drafted = time.perf_counter() - t0
    print(f"{'queue: all drafted':<22} {a.styles} tech packs in {drafted:.2f}s after the last upload")

    def first_version(sid):
        return timed(lambda: client.get(f"/api/styles/{sid}"))[1].json()["versions"][0]["id"]

    vids = list(pool.map(first_version, ids))
    t0 = time.perf_counter()
    res = list(pool.map(lambda v: timed(lambda: client.post(f"/api/versions/{v}/approve")), vids))
    report("approve", [r[0] for r in res], time.perf_counter() - t0)

    t0 = time.perf_counter()
    res = list(pool.map(lambda v: timed(lambda: client.post(f"/api/versions/{v}/send", json={})), vids))
    report("send to 3 factories", [r[0] for r in res], time.perf_counter() - t0)

    t0 = time.perf_counter()
    while True:
        # One cheap call instead of re-reading every style, so the test doesn't slow down what it measures.
        q = timed(lambda: client.get("/api/me"))[1].json()["jobs"].get("send_to_supplier", {})
        if not q.get("queued") and not q.get("running"):
            break
        if time.perf_counter() - t0 > 300:
            raise SystemExit("factory handoff did not finish in 5 minutes")
        time.sleep(0.25)
    handoff = time.perf_counter() - t0
    done = list(pool.map(lambda sid: timed(lambda: client.get(f"/api/styles/{sid}"))[1].json(), ids))
    reqs = [r for d in done for r in d["requests"]]
    quoted = sum(r["status"] == "quoted" for r in reqs)
    tries = sum(r["attempts"] for r in reqs)
    print(f"{'queue: all handed off':<22} {quoted}/{len(reqs)} quoted in {handoff:.2f}s, "
          f"{tries} deliveries for {len(reqs)} orders")

    keys = "&".join(f"key={r['idempotency_key']}" for r in reqs[:150])
    inbox = client.get(f"/sim/suppliers/inbox?{keys}").json()
    print(f"{'factory inbox (sample)':<22} " + ", ".join(f"{i['supplier_code']}: {i['orders']} orders / {i['deliveries']} deliveries" for i in inbox))

    n = a.styles * 5
    t0 = time.perf_counter()
    res = list(pool.map(lambda i: timed(lambda: client.get(f"/api/styles/{ids[i % len(ids)]}")), range(n)))
    report("read style (full)", [r[0] for r in res], time.perf_counter() - t0)

    t0 = time.perf_counter()
    res = list(pool.map(lambda i: timed(lambda: client.get("/api/styles")), range(n // 5)))
    report("list styles", [r[0] for r in res], time.perf_counter() - t0)
    print(f"{'dropped connections':<22} {RECONNECTS} (retried once each)")


if __name__ == "__main__":
    main()
