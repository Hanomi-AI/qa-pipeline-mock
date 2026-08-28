#!/usr/bin/env python3
"""Smoke check: is the pipeline alive and misbehaving as configured?

This is NOT the validation harness the assignment asks for. It only proves the
service works. Deliberately dumb: no tolerance logic, no golden comparison, no
event consumption. That part is the candidate's job.

    python scripts/smoke.py [--n 12] [--base http://localhost:8080]
"""
import argparse
import os
import json
import sys
import time
import urllib.error
import urllib.request

TYPES = ["raw", "compressed", "legacy", "structured"]


def call(method, url, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=12)
    ap.add_argument("--base", default=os.getenv("BASE", "http://localhost:8090"))
    ap.add_argument("--timeout", type=int, default=180)
    a = ap.parse_args()
    B = a.base.rstrip("/")

    st, _ = call("GET", B + "/healthz")
    if st != 200:
        print("FAIL: /healthz returned", st)
        return 1
    print("health          ok")

    ids = []
    for i in range(a.n):
        st, d = call("POST", B + "/jobs", {
            "input_ref": "input_%03d.bin" % (i + 1),
            "input_type": TYPES[i % len(TYPES)],
        })
        if st != 202:
            print("FAIL: POST /jobs returned", st, d)
            return 1
        ids.append(d["job_id"])
    print("submitted       %d jobs" % len(ids))

    # idempotency: same key twice must yield one job
    st1, d1 = call("POST", B + "/jobs", {"input_ref": "idem.bin", "input_type": "raw"},
                   {"Idempotency-Key": "smoke-key-1"})
    st2, d2 = call("POST", B + "/jobs", {"input_ref": "idem.bin", "input_type": "raw"},
                   {"Idempotency-Key": "smoke-key-1"})
    same = d1["job_id"] == d2["job_id"]
    print("idempotency     %s (%s -> %s, %s)" % ("ok" if same else "FAIL", st1, st2, d1["job_id"]))

    # poll to terminal
    deadline = time.time() + a.timeout
    terminal, pending = {}, set(ids)
    while pending and time.time() < deadline:
        for jid in list(pending):
            _, j = call("GET", B + "/jobs/" + jid)
            if j and j["status"] in ("completed", "failed", "cancelled"):
                terminal[jid] = j
                pending.discard(jid)
        if pending:
            time.sleep(1.5)

    counts = {}
    for j in terminal.values():
        counts[j["status"]] = counts.get(j["status"], 0) + 1
    print("terminal        %s" % (counts or "none"))
    if pending:
        print("stuck           %d job(s) never reached a terminal state (HANG_RATE)" % len(pending))
        for jid in list(pending)[:3]:
            print("                  %s" % jid)

    # filter + a completed output
    _, page = call("GET", B + "/jobs?input_type=legacy&limit=100")
    ok_filter = all(j["input_type"] == "legacy" for j in page["jobs"])
    print("filter          %s (%d legacy rows)" % ("ok" if ok_filter else "FAIL", len(page["jobs"])))

    done = next((j for j in terminal.values() if j["status"] == "completed"), None)
    if done:
        out = done["output"]
        ms = out.get("measurements", [])
        drifted = sum(
            1 for m in ms
            if "nominal" in m and abs(m["value"] - m["nominal"]) > m.get("tol_plus", 0)
        )
        dupes = len(ms) - len({m["id"] for m in ms})
        print("sample output   %s  units=%s schema=%s  %d measurements"
              % (done["job_id"], out.get("units"), out.get("schema_version"), len(ms)))
        print("                  %d out of tolerance, %d duplicate id(s)" % (drifted, dupes))
    else:
        print("sample output   none completed")

    _, m = call("GET", B + "/metrics")
    print("metrics         published=%s dlq=%s defects=%s seed=%s"
          % (m.get("events_published_total"), m.get("events_dlq_total"),
             m.get("defects_active"), m.get("seed")))

    print("\nThe service is up. Writing the real harness is the exercise.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
