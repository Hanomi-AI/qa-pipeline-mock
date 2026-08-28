import base64
import json
import uuid
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, Header, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware

from . import config, db, events, models, outputs, worker

_submitted = 0


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init()
    await events.init()
    await worker.start()
    yield
    await worker.stop()
    await events.close()
    await db.close()


app = FastAPI(
    title="qa-pipeline-mock",
    version="1.0.0",
    description="System under test for the Hanomi QA take-home. "
                "It misbehaves on purpose - see the README.",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)


def _job_id() -> str:
    return "j_" + uuid.uuid4().hex[:22]


def _row_to_job(r) -> dict:
    return {
        "job_id": r["id"],
        "input_ref": r["input_ref"],
        "input_type": r["input_type"],
        "status": r["status"],
        "stage": r["stage"],
        "attempts": r["attempts"],
        "output": r["output"],
        "error": r["error"],
        "created_at": r["created_at"].isoformat(),
        "updated_at": r["updated_at"].isoformat(),
    }


def _enc(created_at: datetime, jid: str) -> str:
    raw = json.dumps({"t": created_at.isoformat(), "i": jid}).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _dec(cur: str) -> tuple[str, str]:
    pad = "=" * (-len(cur) % 4)
    try:
        d = json.loads(base64.urlsafe_b64decode(cur + pad))
        return d["t"], d["i"]
    except Exception:
        raise HTTPException(400, "malformed cursor")


# --------------------------------------------------------------------------- #


@app.get("/healthz")
async def healthz():
    return {"ok": True}


@app.get("/metrics")
async def metrics():
    row = await db.fetchrow(
        """SELECT count(*) FILTER (WHERE status='completed') AS completed,
                  count(*) FILTER (WHERE status='failed')    AS failed,
                  count(*)                                   AS total
             FROM jobs"""
    )
    completed = row["completed"]
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
        "jobs_failed_total": row["failed"],
        **worker.counters(),
        **events.counters(),
        "defects_active": sorted(config.DEFECTS),
        "seed": config.SEED,
    }


@app.post("/jobs", status_code=202, response_model=models.JobAccepted)
async def submit(
    body: models.SubmitJob,
    response: Response,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    global _submitted

    if idempotency_key:
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
            return {"job_id": existing["id"], "status": existing["status"]}

    jid = _job_id()
    try:
        await db.execute(
            """INSERT INTO jobs (id, input_ref, input_type, status, attempts, idem_key)
               VALUES ($1,$2,$3,'queued',1,$4)""",
            jid, body.input_ref, body.input_type, idempotency_key,
        )
    except Exception:
        # lost the race on idem_key - return the winner
        if idempotency_key:
            e = await db.fetchrow(
                "SELECT id, status FROM jobs WHERE idem_key = $1", idempotency_key
            )
            if e:
                response.status_code = 200
                return {"job_id": e["id"], "status": e["status"]}
        raise

    await db.execute(
        "INSERT INTO job_attempts (job_id, attempt, status) VALUES ($1,1,'queued')", jid
    )
    _submitted += 1
    await events.publish("job.queued", jid, 1)

    # d6: one orphan terminal event per run - an event whose job has no row
    if config.defect("d6") and _submitted == 5:
        await events.publish_orphan()

    return {"job_id": jid, "status": "queued"}


@app.get("/jobs", response_model=models.JobPage)
async def list_jobs(
    input_type: str | None = Query(default=None),
    status: str | None = Query(default=None),
    created_after: datetime | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None),
):
    if input_type and input_type not in config.INPUT_TYPES:
        raise HTTPException(400, "input_type must be one of " + str(config.INPUT_TYPES))

    where: list[str] = []
    args: list = []

    def arg(v):
        args.append(v)
        return "$" + str(len(args))

    if input_type:
        where.append("input_type = " + arg(input_type))
    if status:
        where.append("status = " + arg(status))
    if created_after:
        # d10: silently ignored when combined with `status`. Each filter is
        # correct on its own, which is why single-filter tests pass.
        if not (config.defect("d10") and status):
            where.append("created_at > " + arg(created_after))

    if cursor:
        t, i = _dec(cursor)
        ts = datetime.fromisoformat(t)
        if config.defect("d9"):
            # Unstable: rows sharing created_at straddle the page boundary and
            # get skipped or repeated.
            where.append("created_at > " + arg(ts))
        else:
            where.append("(created_at, id) > (" + arg(ts) + ", " + arg(i) + ")")

    order = "created_at" if config.defect("d9") else "created_at, id"

    sql = "SELECT * FROM jobs"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY " + order + " LIMIT " + arg(limit + 1)

    rows = await db.fetch(sql, *args)
    more = len(rows) > limit
    # d11: off-by-one on the FIRST page only - returns limit+1 rows. A test
    # that checks `len(page) <= limit` on page one catches it; one that only
    # checks the default page size, or only later pages, does not.
    if config.defect("d11") and cursor is None:
        rows = rows[: limit + 1]
    else:
        rows = rows[:limit]
    nxt = _enc(rows[-1]["created_at"], rows[-1]["id"]) if (more and rows) else None
    return {"jobs": [_row_to_job(r) for r in rows], "next_cursor": nxt}


@app.get("/jobs/{job_id}", response_model=models.Job)
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

    return job


@app.post("/jobs/{job_id}/retry", status_code=202, response_model=models.Job)
async def retry(job_id: str):
    r = await db.fetchrow("SELECT * FROM jobs WHERE id = $1", job_id)
    if r is None:
        raise HTTPException(404, "no such job")
    if r["status"] not in ("completed", "failed", "cancelled"):
        raise HTTPException(409, "cannot retry from status " + r["status"])

    n = r["attempts"] + 1
    # RETURNING, so the response reflects the state this endpoint produced. A
    # follow-up SELECT would race the worker pool, which re-claims a queued job
    # within ~250ms - the response would then describe someone else's write.
    if config.defect("d12"):
        # stage is not cleared: a queued job still reports the stage it died
        # at. Asserting on `status` alone will not see this.
        updated = await db.fetchrow(
            """UPDATE jobs SET status='queued', attempts=$2, error=NULL, output=NULL,
                               updated_at=now() WHERE id=$1 RETURNING *""",
            job_id, n,
        )
    else:
        updated = await db.fetchrow(
            """UPDATE jobs SET status='queued', attempts=$2, error=NULL, output=NULL,
                               stage=NULL, updated_at=now() WHERE id=$1 RETURNING *""",
            job_id, n,
        )
    await db.execute(
        "INSERT INTO job_attempts (job_id, attempt, status) VALUES ($1,$2,'queued')",
        job_id, n,
    )
    # sequence restarts at 1 for the new attempt - see events.next_sequence
    await events.publish("job.queued", job_id, n)
    return _row_to_job(updated)


@app.delete("/jobs/{job_id}", response_model=models.Job)
async def cancel(job_id: str):
    r = await db.fetchrow("SELECT * FROM jobs WHERE id = $1", job_id)
    if r is None:
        raise HTTPException(404, "no such job")
    if r["status"] in ("completed", "failed", "cancelled"):
        raise HTTPException(409, r["status"] + " is terminal and immutable")
    await db.execute(
        "UPDATE jobs SET status='cancelled', updated_at=now() WHERE id=$1", job_id
    )
    await events.publish("job.cancelled", job_id, r["attempts"], r["stage"])
    return _row_to_job(await db.fetchrow("SELECT * FROM jobs WHERE id = $1", job_id))


@app.post("/webhooks", status_code=201, response_model=models.Webhook)
async def register_webhook(body: models.WebhookIn):
    wid = "w_" + uuid.uuid4().hex[:16]
    await db.execute("INSERT INTO webhooks (id, url) VALUES ($1,$2)", wid, body.url)
    return {"id": wid, "url": body.url}


@app.get("/goldens/{input_ref}")
async def get_golden(input_ref: str, input_type: str = Query(...)):
    """Convenience: the expected output for an input. Deterministic, so you can
    regenerate ./goldens at any time instead of committing them."""
    if input_type not in config.INPUT_TYPES:
        raise HTTPException(400, "input_type must be one of " + str(config.INPUT_TYPES))
    return outputs.golden(input_ref, input_type)
