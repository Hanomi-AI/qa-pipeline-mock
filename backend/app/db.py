import asyncio
import json

import asyncpg
from . import config

_pool: asyncpg.Pool | None = None

DDL = """
CREATE TABLE IF NOT EXISTS jobs (
  id          TEXT PRIMARY KEY,
  input_ref   TEXT        NOT NULL,
  input_type  TEXT        NOT NULL CHECK (input_type IN ('raw','compressed','legacy','structured')),
  status      TEXT        NOT NULL CHECK (status IN ('queued','processing','completed','failed','cancelled')),
  stage       TEXT,
  attempts    INT         NOT NULL DEFAULT 1,
  output      JSONB,
  error       JSONB,
  idem_key    TEXT UNIQUE,
  -- Second resolution on purpose: rows submitted in the same second share a
  -- created_at, so ties are normal and stable pagination REQUIRES a tiebreak.
  created_at  TIMESTAMPTZ NOT NULL DEFAULT date_trunc('second', now()),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS job_attempts (
  job_id      TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  attempt     INT  NOT NULL,
  status      TEXT NOT NULL,
  started_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ,
  PRIMARY KEY (job_id, attempt)
);

CREATE TABLE IF NOT EXISTS webhooks (
  id  TEXT PRIMARY KEY,
  url TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS jobs_created_idx ON jobs (created_at);
"""


async def init(attempts: int = 30, delay: float = 2.0) -> None:
    """Connect with backoff, then apply the schema."""
    global _pool
    last: Exception | None = None
    for i in range(attempts):
        try:
            _pool = await asyncpg.create_pool(
                config.DATABASE_URL, min_size=2, max_size=12,
                init=_register_json,
            )
            break
        except Exception as e:                       # noqa: BLE001
            last = e
            print(f"[db] postgres not ready ({i + 1}/{attempts}): {e}", flush=True)
            await asyncio.sleep(delay)
    else:
        raise RuntimeError(f"could not reach postgres at {config.DATABASE_URL}") from last

    async with _pool.acquire() as c:
        await c.execute(DDL)


async def _register_json(conn: asyncpg.Connection) -> None:
    await conn.set_type_codec(
        "jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
    )


async def close() -> None:
    if _pool:
        await _pool.close()


def pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("db not initialised")
    return _pool


async def fetch(q: str, *a):
    async with pool().acquire() as c:
        return await c.fetch(q, *a)


async def fetchrow(q: str, *a):
    async with pool().acquire() as c:
        return await c.fetchrow(q, *a)


async def execute(q: str, *a):
    async with pool().acquire() as c:
        return await c.execute(q, *a)
