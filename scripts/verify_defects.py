#!/usr/bin/env python3
"""Interviewer tool: prove each defect in DEFECTS actually reproduces.

Run it with a profile enabled to confirm the trap is armed, and with DEFECTS=""
to confirm the correct path passes. If a check says PASS with the defect on, the
defect is unreachable and worthless - fix it before using the profile.

    python scripts/verify_defects.py
"""
import json
import sys
import time
import urllib.error
import urllib.request

import os
BASE = os.getenv("BASE", "http://localhost:8080")


def call(method, path, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw}


def wait_terminal(jid, timeout=120):
    end = time.time() + timeout
    while time.time() < end:
        _, j = call("GET", "/jobs/" + jid)
        if j and j["status"] in ("completed", "failed", "cancelled"):
            return j
        time.sleep(1.0)
    return None


def report(name, reproduced, detail):
    tag = "REPRODUCES" if reproduced else "correct   "
    print("  %-4s %s  %s" % (name, tag, detail))
    return reproduced


def main():
    _, m = call("GET", "/metrics")
    active = set(m.get("defects_active") or [])
    print("active profile: %s\n" % (sorted(active) or "none (expecting all checks correct)"))

    types = ["raw", "compressed", "legacy", "structured"]
    ids = []
    for i in range(12):
        _, d = call("POST", "/jobs", {
            "input_ref": "verify_%03d.bin" % i, "input_type": types[i % 4]})
        ids.append(d["job_id"])
    for jid in ids:
        wait_terminal(jid)

    results = {}

    # ---- d11: first page returns limit+1 ----
    _, p = call("GET", "/jobs?limit=5")
    results["d11"] = report("d11", len(p["jobs"]) > 5,
                            "GET /jobs?limit=5 returned %d rows" % len(p["jobs"]))

    # ---- d10: created_after ignored when combined with status ----
    _, allp = call("GET", "/jobs?limit=100")
    future = "2099-01-01T00:00:00Z"
    _, only_after = call("GET", "/jobs?created_after=" + future)
    _, combined = call("GET", "/jobs?status=completed&created_after=" + future)
    results["d10"] = report(
        "d10", len(combined["jobs"]) > 0 and len(only_after["jobs"]) == 0,
        "created_after alone -> %d rows; with status -> %d rows"
        % (len(only_after["jobs"]), len(combined["jobs"])))

    # ---- d13: same Idempotency-Key, different body ----
    k = "verify-idem-%d" % int(time.time())
    s1, a = call("POST", "/jobs", {"input_ref": "a.bin", "input_type": "raw"},
                 {"Idempotency-Key": k})
    s2, b = call("POST", "/jobs", {"input_ref": "TOTALLY-DIFFERENT.bin",
                                   "input_type": "legacy"},
                 {"Idempotency-Key": k})
    results["d13"] = report(
        "d13", s2 != 409,
        "key reused with a different body -> HTTP %s%s"
        % (s2, "" if s2 == 409 else " (should be 409)"))

    # ---- d12 / d14 / d17: need a completed job and a retry ----
    _, done = call("GET", "/jobs?status=completed&limit=1")
    if done["jobs"]:
        jid = done["jobs"][0]["job_id"]
        # The retry response IS the job resource post-update. Using it avoids
        # racing the worker, which re-claims a queued job within ~250ms.
        _, pre = call("GET", "/jobs/" + jid)
        before_attempts = pre["attempts"]
        _, after = call("POST", "/jobs/%s/retry" % jid)
        results["d12"] = report(
            "d12", after["status"] == "queued" and after["stage"] is not None,
            "retry response: status=%s stage=%s (stage should be null)"
            % (after["status"], after["stage"]))
        # d14 lives in GET /jobs/{id}, not in the retry response (which returns
        # the row directly). Read it from the endpoint that actually has it.
        expect = before_attempts + 1
        _, got = call("GET", "/jobs/" + jid)
        results["d14"] = report(
            "d14", got["attempts"] != expect,
            "GET reports attempts=%s after retry (row says %s, expected %s)"
            % (got["attempts"], after["attempts"], expect))

        wait_terminal(jid)
        _, am = call("GET", "/metrics")
        # Count completed rows directly rather than trusting another metric.
        seen, cur = 0, None
        while True:
            q = "/jobs?status=completed&limit=100" + ("&cursor=" + cur if cur else "")
            _, page = call("GET", q)
            seen += len(page["jobs"])
            cur = page.get("next_cursor")
            if not cur:
                break
        results["d17"] = report(
            "d17", am["jobs_completed_total"] != seen,
            "/metrics says completed=%s, actual completed rows=%s"
            % (am["jobs_completed_total"], seen))
    else:
        print("  ---- skipped d12/d14/d17: no completed job to retry")

    # ---- d16: numbers serialised as strings for input_type=raw ----
    _, raws = call("GET", "/jobs?input_type=raw&status=completed&limit=5")
    strung = None
    for j in raws["jobs"]:
        for meas in (j["output"] or {}).get("measurements", []):
            if isinstance(meas.get("value"), str):
                strung = (j["job_id"], meas["id"], meas["value"])
                break
        if strung:
            break
    results["d16"] = report(
        "d16", strung is not None,
        "found %s" % (("%s.%s value=%r (str)" % strung) if strung else "no stringified numbers"))

    print()
    armed = {k for k, v in results.items() if v}
    expected = active & set(results)
    missing = expected - armed
    unexpected = armed - active

    print("armed        : %s" % (sorted(armed) or "none"))
    if missing:
        print("NOT ARMED    : %s  <- enabled but did not reproduce" % sorted(missing))
    if unexpected:
        print("LEAKING      : %s  <- reproduced while NOT enabled" % sorted(unexpected))
    if not missing and not unexpected:
        print("verdict      : profile behaves exactly as configured")
    return 1 if (missing or unexpected) else 0


if __name__ == "__main__":
    sys.exit(main())
