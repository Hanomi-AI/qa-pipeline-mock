"""The fake pipeline. A pool of workers claims queued jobs and walks each one
through five stages, publishing events as it goes."""
import asyncio
import json
from datetime import datetime, timezone

import httpx

from . import chaos, config, db, events, outputs

_tasks: list[asyncio.Task] = []
_stop = asyncio.Event()
# Distinct names on purpose. The DB-derived counts in /metrics are
# authoritative; these are the worker's own process-local tallies and would
# silently shadow them if they shared a key.
_counters = {"worker_completions_total": 0, "worker_failures_total": 0}


def counters() -> dict[str, int]:
    return dict(_counters)


async def start() -> None:
    _stop.clear()
    for i in range(config.WORKER_COUNT):
        _tasks.append(asyncio.create_task(_loop(i), name=f"worker-{i}"))


async def stop() -> None:
    _stop.set()
    for t in _tasks:
        t.cancel()
    await asyncio.gather(*_tasks, return_exceptions=True)
    _tasks.clear()


async def _claim() -> dict | None:
    """Atomically take one queued job. SKIP LOCKED keeps the pool from
    contending, and means two workers never claim the same row."""
    async with db.pool().acquire() as c:
        async with c.transaction():
            row = await c.fetchrow(
                """
                SELECT id, input_ref, input_type, attempts FROM jobs
                 WHERE status = 'queued'
                 ORDER BY created_at
                   FOR UPDATE SKIP LOCKED
                 LIMIT 1
                """
            )
            if row is None:
                return None
            await c.execute(
                "UPDATE jobs SET status='processing', stage=$2, updated_at=now() WHERE id=$1",
                row["id"], config.STAGES[0],
            )
            return dict(row)


async def _loop(n: int) -> None:
    while not _stop.is_set():
        try:
            job = await _claim()
            if job is None:
                await asyncio.sleep(0.25)
                continue
            await _run(job)
        except asyncio.CancelledError:
            raise
        except Exception as e:                       # keep the pool alive
            print(f"[worker-{n}] {type(e).__name__}: {e}", flush=True)
            await asyncio.sleep(0.5)


async def _run(job: dict) -> None:
    jid, attempt = job["id"], job["attempts"]
    stage_times: list[dict] = []

    # HANG_RATE - enters processing and never leaves. No event, no terminal state.
    if chaos.hit(config.HANG_RATE):
        return

    fail_at = chaos.pick(config.STAGES) if chaos.hit(config.FAIL_RATE) else None

    for stage in config.STAGES:
        await events.publish("job.stage.started", jid, attempt, stage)
        ms = int(chaos.jitter_ms(200, 1500) * config.LATENCY_MS)
        await asyncio.sleep(ms / 1000.0)
        await db.execute(
            "UPDATE jobs SET stage=$2, updated_at=now() WHERE id=$1", jid, stage
        )

        if stage == fail_at:
            err = {"stage": stage, "code": "stage_error",
                   "message": f"{stage} failed deterministically under this seed"}
            await db.execute(
                """UPDATE jobs SET status='failed', error=$2, output=NULL, updated_at=now()
                    WHERE id=$1""", jid, err,
            )
            await db.execute(
                """UPDATE job_attempts SET status='failed', finished_at=now()
                    WHERE job_id=$1 AND attempt=$2""", jid, attempt,
            )
            _counters["worker_failures_total"] += 1
            await events.publish("job.failed", jid, attempt, stage, err)
            return

        stage_times.append({"name": stage, "ms": ms})

        # d7: publish stage.completed BEFORE the row is updated, so a consumer
        #     that reads the DB on receipt sees stale state.
        if config.defect("d7") and stage == "assemble":
            await events.publish("job.stage.completed", jid, attempt, stage)
            await asyncio.sleep(0.35)
        else:
            await events.publish("job.stage.completed", jid, attempt, stage)

    out = outputs.realise(jid, job["input_ref"], job["input_type"], stage_times)
    await db.execute(
        """UPDATE jobs SET status='completed', output=$2, stage=$3, updated_at=now()
            WHERE id=$1""", jid, out, config.STAGES[-1],
    )
    await db.execute(
        """UPDATE job_attempts SET status='completed', finished_at=now()
            WHERE job_id=$1 AND attempt=$2""", jid, attempt,
    )
    _counters["worker_completions_total"] += 1

    await events.publish("job.completed", jid, attempt, config.STAGES[-1])
    await _fire_webhooks(jid)


async def _fire_webhooks(job_id: str) -> None:
    rows = await db.fetch("SELECT url FROM webhooks")
    if not rows:
        return
    body = {"job_id": job_id, "status": "completed"}
    async with httpx.AsyncClient(timeout=3.0) as cl:
        for r in rows:
            n = 2 if chaos.hit(config.DUPLICATE_WEBHOOKS) else 1
            for _ in range(n):
                try:
                    await cl.post(r["url"], json=body)
                except Exception:
                    pass          # at-least-once means best effort, not guaranteed
