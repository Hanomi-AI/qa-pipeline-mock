"""Event publishing over RabbitMQ.

Guarantees are deliberately weak, and weak in the ways real brokers are:
  * at-least-once   - EVENT_DUP_RATE republishes
  * lossy           - EVENT_DROP_RATE silently discards
  * unordered       - EVENT_REORDER_RATE delays a publish behind the next one
  * poisonable      - POISON_RATE emits malformed bodies bound for the DLQ

`sequence` is monotonic per (job_id, attempt) and RESTARTS AT 1 on retry.
`occurred_at` carries up to 2s of jitter. Neither field can be sorted on alone.
"""
import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone

import aio_pika

from . import chaos, config

_conn: aio_pika.RobustConnection | None = None
_chan: aio_pika.abc.AbstractChannel | None = None
_ex: aio_pika.abc.AbstractExchange | None = None

# (job_id, attempt) -> next sequence number
_seq: dict[tuple[str, int], int] = {}
_published = 0
_dlq_sent = 0
_pending_reorder: list[tuple[str, aio_pika.Message]] = []


async def init(attempts: int = 30, delay: float = 2.0) -> None:
    """Connect with backoff. A broker that reports healthy is not necessarily
    accepting AMQP connections yet, and candidates will restart things."""
    global _conn, _chan, _ex
    last: Exception | None = None
    for i in range(attempts):
        try:
            _conn = await aio_pika.connect_robust(config.AMQP_URL)
            break
        except Exception as e:                       # noqa: BLE001
            last = e
            print(f"[events] broker not ready ({i + 1}/{attempts}): {e}", flush=True)
            await asyncio.sleep(delay)
    else:
        raise RuntimeError(f"could not reach broker at {config.AMQP_URL}") from last
    _chan = await _conn.channel()
    _ex = await _chan.declare_exchange(
        config.EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True
    )
    dlx = await _chan.declare_exchange(
        config.DLX, aio_pika.ExchangeType.FANOUT, durable=True
    )
    dlq = await _chan.declare_queue(config.DLQ, durable=True)
    await dlq.bind(dlx)
    q = await _chan.declare_queue(
        config.QUEUE, durable=True,
        arguments={"x-dead-letter-exchange": config.DLX},
    )
    await q.bind(_ex, routing_key="job.#")


async def close() -> None:
    if _conn:
        await _conn.close()


def counters() -> dict[str, int]:
    return {"events_published_total": _published, "events_dlq_total": _dlq_sent}


def next_sequence(job_id: str, attempt: int) -> int:
    key = (job_id, attempt)
    n = _seq.get(key, 0) + 1
    _seq[key] = n
    return n


def _skewed_now() -> str:
    """Publisher clock. Up to 2s of skew either way, so occurred_at is not a
    reliable sort key inside a 2 second window."""
    skew = timedelta(milliseconds=chaos.jitter_ms(-2000, 2000))
    return (datetime.now(timezone.utc) + skew).isoformat().replace("+00:00", "Z")


def envelope(event_type: str, job_id: str, attempt: int,
             stage: str | None = None, payload: dict | None = None) -> dict:
    return {
        "event_id": "e_" + uuid.uuid4().hex[:22],
        "event_type": event_type,
        "job_id": job_id,
        "stage": stage,
        "attempt": attempt,
        "sequence": next_sequence(job_id, attempt),
        "occurred_at": _skewed_now(),
        "payload": payload or {},
    }


async def publish(event_type: str, job_id: str, attempt: int,
                  stage: str | None = None, payload: dict | None = None) -> None:
    global _published, _dlq_sent

    if _ex is None:
        return

    ev = envelope(event_type, job_id, attempt, stage, payload)
    rk = event_type

    # d5: terminal event never published, though the row says completed
    if config.defect("d5") and event_type == "job.completed" and chaos.hit(0.033):
        return

    # POISON_RATE: malformed body. The queue's DLX sends it to the DLQ once a
    # consumer rejects it - a consumer that crashes instead will wedge.
    if chaos.hit(config.POISON_RATE):
        bad = aio_pika.Message(
            body=b'{"event_type": null, "this_is": "not a valid envelope"',
            content_type="application/json",
        )
        await _ex.publish(bad, routing_key=rk)
        _published += 1
        _dlq_sent += 1
        return

    if chaos.hit(config.EVENT_DROP_RATE):
        return

    msg = aio_pika.Message(
        body=json.dumps(ev).encode(),
        content_type="application/json",
        message_id=ev["event_id"],
    )

    # reorder: hold this one back and flush it after the next publish
    if chaos.hit(config.EVENT_REORDER_RATE):
        _pending_reorder.append((rk, msg))
        return

    await _ex.publish(msg, routing_key=rk)
    _published += 1

    if chaos.hit(config.EVENT_DUP_RATE):
        await _ex.publish(msg, routing_key=rk)
        _published += 1

    while _pending_reorder:
        prk, pmsg = _pending_reorder.pop(0)
        await _ex.publish(pmsg, routing_key=prk)
        _published += 1


async def publish_orphan() -> None:
    """d6: a terminal event for a job_id that has no row. Simulates the classic
    dual-write failure - the event landed, the transaction did not."""
    if _ex is None:
        return
    ghost = "j_" + uuid.uuid4().hex[:22]
    await publish("job.completed", ghost, 1, stage="assemble")
