"""One-off patcher that adds the subtle API defects (d10-d17) to app/main.py.

Kept in the repo as a record of what was inserted and why. Each defect is
env-gated and inert unless its id appears in DEFECTS.
"""
import io
import sys

MAIN = "backend/app/main.py"
OUT = "backend/app/outputs.py"

patches = []

# --------------------------------------------------------------------------- #
# d17: /metrics double-counts retried jobs, so metrics disagree with the DB.
#      Found only by cross-checking /metrics against a COUNT(*) query.
# --------------------------------------------------------------------------- #
patches.append((MAIN, """    return {
        "jobs_submitted_total": _submitted,
        "jobs_in_db_total": row["total"],
        "jobs_completed_total": row["completed"],
        "jobs_failed_total": row["failed"],""",
"""    completed = row["completed"]
    if config.defect("d17"):
        # Counts one extra per retried job. /metrics and the jobs table
        # disagree; neither alone looks wrong.
        extra = await db.fetchrow(
            "SELECT count(*) AS n FROM jobs WHERE attempts > 1 AND status='completed'"
        )
        completed += extra["n"]

    return {
        "jobs_submitted_total": _submitted,
        "jobs_in_db_total": row["total"],
        "jobs_completed_total": completed,
        "jobs_failed_total": row["failed"],"""))

# --------------------------------------------------------------------------- #
# d13: Idempotency-Key is treated as globally unique regardless of the body.
#      Reusing a key with a DIFFERENT payload silently returns the first job
#      instead of rejecting. The happy-path idempotency test still passes.
# --------------------------------------------------------------------------- #
patches.append((MAIN, """    if idempotency_key:
        existing = await db.fetchrow(
            "SELECT id, status FROM jobs WHERE idem_key = $1", idempotency_key
        )
        if existing:
            # Same key, same job. 200 rather than 202: nothing new was accepted.
            response.status_code = 200
            return {"job_id": existing["id"], "status": existing["status"]}""",
"""    if idempotency_key:
        existing = await db.fetchrow(
            "SELECT id, status, input_ref, input_type FROM jobs WHERE idem_key = $1",
            idempotency_key,
        )
        if existing:
            reused_with_different_body = (
                existing["input_ref"] != body.input_ref
                or existing["input_type"] != body.input_type
            )
            if reused_with_different_body and not config.defect("d13"):
                # Correct: a key pinned to one payload cannot be reused for another.
                raise HTTPException(
                    409,
                    "Idempotency-Key already used with a different body",
                )
            # Same key, same job. 200 rather than 202: nothing new was accepted.
            response.status_code = 200
            return {"job_id": existing["id"], "status": existing["status"]}"""))

# --------------------------------------------------------------------------- #
# d10: `status` and `created_after` work individually, but combining them
#      drops the created_after clause. Only a test that COMBINES filters finds it.
# d11: the first page returns limit+1 rows. Later pages are correct.
# --------------------------------------------------------------------------- #
patches.append((MAIN, """    if status:
        where.append("status = " + arg(status))
    if created_after:
        where.append("created_at > " + arg(created_after))""",
"""    if status:
        where.append("status = " + arg(status))
    if created_after:
        # d10: silently ignored when combined with `status`. Each filter is
        # correct on its own, which is why single-filter tests pass.
        if not (config.defect("d10") and status):
            where.append("created_at > " + arg(created_after))"""))

patches.append((MAIN, """    rows = await db.fetch(sql, *args)
    more = len(rows) > limit
    rows = rows[:limit]""",
"""    rows = await db.fetch(sql, *args)
    more = len(rows) > limit
    # d11: off-by-one on the FIRST page only - returns limit+1 rows. A test
    # that checks `len(page) <= limit` on page one catches it; one that only
    # checks the default page size, or only later pages, does not.
    if config.defect("d11") and cursor is None:
        rows = rows[: limit + 1]
    else:
        rows = rows[:limit]"""))

# --------------------------------------------------------------------------- #
# d14: `attempts` is served from a COUNT of job_attempts rows while retry
#      increments jobs.attempts. After one retry the two sources disagree.
#      Found only by reconciling the API against the tables.
# --------------------------------------------------------------------------- #
patches.append((MAIN, """@app.get("/jobs/{job_id}", response_model=models.Job)
async def get_job(job_id: str):
    r = await db.fetchrow("SELECT * FROM jobs WHERE id = $1", job_id)
    if r is None:
        raise HTTPException(404, "no such job")
    return _row_to_job(r)""",
"""@app.get("/jobs/{job_id}", response_model=models.Job)
async def get_job(job_id: str):
    r = await db.fetchrow("SELECT * FROM jobs WHERE id = $1", job_id)
    if r is None:
        raise HTTPException(404, "no such job")
    job = _row_to_job(r)

    if config.defect("d14"):
        # Reads the count from the wrong place. Correct until the first retry,
        # then permanently one behind jobs.attempts.
        n = await db.fetchrow(
            "SELECT count(*) AS n FROM job_attempts WHERE job_id=$1 AND status<>'queued'",
            job_id,
        )
        job["attempts"] = max(1, n["n"])

    return job"""))

# --------------------------------------------------------------------------- #
# d12: retry leaves the old terminal `stage` in place. status flips to
#      'queued' correctly, so a test asserting only on status passes.
# --------------------------------------------------------------------------- #
patches.append((MAIN, """    n = r["attempts"] + 1
    await db.execute(
        \"\"\"UPDATE jobs SET status='queued', attempts=$2, error=NULL, output=NULL,
                           stage=NULL, updated_at=now() WHERE id=$1\"\"\",
        job_id, n,
    )""",
"""    n = r["attempts"] + 1
    if config.defect("d12"):
        # stage is not cleared: a queued job still reports the stage it died
        # at. Asserting on `status` alone will not see this.
        await db.execute(
            \"\"\"UPDATE jobs SET status='queued', attempts=$2, error=NULL, output=NULL,
                               updated_at=now() WHERE id=$1\"\"\",
            job_id, n,
        )
    else:
        await db.execute(
            \"\"\"UPDATE jobs SET status='queued', attempts=$2, error=NULL, output=NULL,
                               stage=NULL, updated_at=now() WHERE id=$1\"\"\",
            job_id, n,
        )"""))

# --------------------------------------------------------------------------- #
# d16: numeric measurement values serialised as STRINGS for one input_type.
#      A comparator that coerces with float() passes; one that validates types
#      against the schema does not.
# --------------------------------------------------------------------------- #
patches.append((OUT, """    return out""",
"""    # d16: numbers as strings, for one input_type only. float("42.53") == 42.53,
    # so a coercing comparator sails past a genuine schema violation.
    if config.defect("d16") and input_type == "raw":
        for m in out["measurements"]:
            for k in ("value", "nominal", "tol_plus", "tol_minus"):
                if k in m and isinstance(m[k], (int, float)):
                    m[k] = str(m[k])

    return out"""))


def main() -> int:
    applied = 0
    for path, old, new in patches:
        s = io.open(path, encoding="utf-8").read()
        # Detect by the defect id in the inserted comment, not by a prefix of
        # `new` - those first characters are identical to `old`.
        marker = next(
            (w for w in new.split() if w.startswith("d1") and w.endswith(":")), None
        )
        if marker and marker in s:
            print("  skip (already applied):", path, marker)
            continue
        if old not in s:
            print("  MISS:", path, "|", old.strip()[:60])
            continue
        s = s.replace(old, new, 1)
        io.open(path, "w", encoding="utf-8").write(s)
        applied += 1
        print("  ok:", path, "|", new.strip().split("\n")[0][:56])
    print("applied %d/%d" % (applied, len(patches)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
