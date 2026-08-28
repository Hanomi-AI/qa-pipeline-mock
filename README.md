# qa-pipeline-mock

The system under test for the Hanomi QA take-home. An async, multi-stage job pipeline —
FastAPI + PostgreSQL + RabbitMQ, with a small Next.js UI — that **misbehaves on purpose**.

It exists to be tested. Your job is to write the harness and the test suite that prove whether it is
behaving. Nothing in this repo is production code and none of it is what you're graded on.

---

## Run it

```bash
git clone <this repo>
cd qa-pipeline-mock
cp .env.example .env      # optional; every value has a default
docker compose up --build -d
```

| | |
|---|---|
| API + OpenAPI docs | http://localhost:8080/docs |
| UI | http://localhost:3000 |
| RabbitMQ management | http://localhost:15672 — `guest` / `guest` |
| Postgres | `localhost:5432`, `pipeline` / `pipeline` / `pipeline` |

**Ports already taken?** Every host port is configurable — set `API_PORT`, `UI_PORT`, `PG_PORT`,
`AMQP_PORT`, `RABBIT_UI_PORT` in `.env`. Nothing else needs to change.

```bash
make up        # build and start
make logs      # follow the backend
make smoke     # submit a batch and print what happened
make reset     # wipe the database and queues, restart
make down      # stop and remove volumes
```

`make smoke` is a liveness check, not a harness — no tolerance logic, no golden comparison, no event
consumption. Building those is the exercise.

---

## What it does

A job walks through five stages: `ingest → parse → analyze → compute → assemble`. Each stage takes a
seeded 200–1500 ms, publishes `job.stage.started` / `job.stage.completed`, and can fail. On success the
job's output is written to Postgres as JSONB and `job.completed` is published.

`input_type` is one of `raw`, `compressed`, `legacy`, `structured`.

### API

| Method | Path | Notes |
|---|---|---|
| `POST` | `/jobs` | `{ input_ref, input_type }`. `Idempotency-Key` header honoured. → `202 { job_id, status }` |
| `GET` | `/jobs/{id}` | Full job resource |
| `GET` | `/jobs` | Filters `input_type`, `status`, `created_after`; paginated by `limit` + `cursor` |
| `POST` | `/jobs/{id}/retry` | New attempt; `job_attempts` history preserved |
| `DELETE` | `/jobs/{id}` | Cancel — legal only from `queued` / `processing` |
| `POST` | `/webhooks` | Register a callback URL. Fired on terminal state, **at-least-once** |
| `GET` | `/healthz`, `/metrics` | Liveness and counters |
| `GET` | `/goldens/{input_ref}?input_type=` | The expected output for an input — see below |

`queued → processing → completed | failed | cancelled`. Terminal states are immutable.

**Two naming details that are deliberate, not typos:**

- The job resource exposes `attempts` (a count); events carry `attempt` (a 1-based index).
- `created_at` is stored at **second** resolution, so jobs submitted in the same second share a
  timestamp. Ties are normal, and stable pagination therefore requires a tiebreak.

### Goldens are computed, not committed

`GET /goldens/{input_ref}?input_type=` returns the expected output for any input. It is a pure function of
`(input_ref, input_type)` — same input, same golden, forever. Fetch them once into `./goldens` and commit
those, or regenerate on demand; both are fine.

A completed job's actual output is the golden with whatever misbehaviour is currently switched on applied
to it. So a diff between the two is always meaningful.

### Events

Topic exchange `pipeline.events`, queue `pipeline.events.q` bound to `job.#`, dead-lettering to
`pipeline.events.dlq`.

```json
{
  "event_id": "e_01H...", "event_type": "job.stage.completed",
  "job_id": "j_01H...", "stage": "compute",
  "attempt": 1, "sequence": 4,
  "occurred_at": "2026-08-28T09:12:44.118Z", "payload": {}
}
```

The guarantees are weak on purpose, and weak in the ways real brokers are:

- Delivery is **at-least-once**. Duplicates happen.
- No global ordering. Per-job ordering is best-effort.
- **`sequence` restarts at 1 on retry**, so it is monotonic per `(job_id, attempt)` and not across attempts.
- **`occurred_at` carries up to ±2 s of publisher clock skew**, so it is not a reliable sort key inside a
  two-second window.
- Sorting on either field alone is wrong. Reconstructing true order needs the state machine too.
- Messages can be lost, and malformed ones are published on purpose.

---

## Misbehaviour knobs

Set these in `.env`. All are rates in `0.0–1.0` and all default to something mild.

| Knob | Default | Effect |
|---|---|---|
| `SEED` | `20260828` | Seeds every random decision. Same seed, same run. |
| `WORKER_COUNT` | `4` | Size of the worker pool |
| `FAIL_RATE` | `0.06` | Jobs that fail at a random stage |
| `LATENCY_MS` | `1.0` | Multiplier on stage duration |
| `HANG_RATE` | `0.01` | Jobs that enter `processing` and never leave |
| `DRIFT_PCT` | `0.04` | Measurements nudged just past tolerance |
| `SCHEMA_DRIFT` | `0.02` | Outputs emitted as `schema_version: 1.3` with a renamed field |
| `UNIT_FLIP` | `0.0` | Declares one `units`, reports values in the other |
| `DUPLICATE_WEBHOOKS` | `0.10` | Webhook calls sent twice |
| `EVENT_DUP_RATE` | `0.08` | Events published twice |
| `EVENT_DROP_RATE` | `0.03` | Events silently dropped |
| `EVENT_REORDER_RATE` | `0.10` | Events published out of order within a job |
| `POISON_RATE` | `0.005` | Malformed events, bound for the DLQ |

Everything is driven from one seeded stream, so a run is reproducible: same `SEED` and same knobs give the
same failures, which means a bug you report can be checked.

---

## Defect profiles

Beyond the statistical knobs there are **discrete, deterministic defects**, each with an id. They are off
by default. Turn them on with a comma-separated list:

```bash
DEFECTS=d1,d10,d13        # backend
UI_DEFECTS=u1,u2          # frontend (build-time, so rebuild after changing)
```

`GET /metrics` reports which are active, so you can always tell what you're up against.

### The catalogue is public, and that is the point

You can read exactly what each defect does below and in the source. Knowing a defect exists does not write
the test that catches it — that's the whole skill being measured. Several of these pass a reasonable-looking
test suite and only fall over under a careful one.

#### Output and comparison

| id | What breaks |
|---|---|
| `d1` | For `input_type=legacy`, `M-007` lands exactly `tol_plus + 1e-4` past the limit. A comparison that rounds the boundary away accepts it. |
| `d3` | `M-005` is silently absent from every `structured` output. Iterating the *output* misses it; iterating the *golden* catches it. |
| `d4` | `M-003` appears **twice** in `compressed` outputs with different values. Match-by-id must detect the collision, not take first-or-last. |
| `d16` | For `input_type=raw`, numeric fields are serialised as **strings**. `float("42.53") == 42.53`, so a coercing comparator sails past a real schema violation. |

#### API

| id | What breaks |
|---|---|
| `d9` | `GET /jobs` drops the `id` tiebreak, so rows sharing a `created_at` are skipped or repeated across page boundaries. |
| `d10` | `created_after` is silently ignored **when combined with** `status`. Each filter is correct alone. |
| `d11` | The **first** page returns `limit + 1` rows. Later pages are correct. |
| `d12` | `retry` leaves the old terminal `stage` in place. `status` flips to `queued` correctly, so asserting on status alone passes. |
| `d13` | An `Idempotency-Key` can be reused with a **different body** and silently returns the first job instead of `409`. The same-key-same-body case still works. |
| `d14` | `GET /jobs/{id}` serves `attempts` from a count of the wrong rows, so after a retry the API and the table disagree. |
| `d17` | `/metrics` double-counts retried jobs, so the metric and the database disagree. Neither looks wrong on its own. |

#### Events

| id | What breaks |
|---|---|
| `d5` | Roughly 1 in 30 jobs reach `completed` in Postgres with **no `job.completed` ever published**. |
| `d6` | One `job.completed` per run fires for a `job_id` that has **no row** — a simulated dual-write failure. |
| `d7` | The `assemble` stage event is published **before** the row is updated, so a consumer that reads the DB on receipt sees stale state. |

#### UI

| id | What breaks |
|---|---|
| `u1` | The job-count label is fetched without the filter, so it disagrees with the table whenever a filter is applied. |
| `u2` | Detail-page polling only stops on `completed`, so a `failed` or `cancelled` job is polled forever — a leaked timer. |
| `u3` | `Cancel` stays enabled on a terminal job, so clicking it `409`s and surfaces a raw error. |

The job list also updates **optimistically** — a row appears before the server confirms it. That is not a
defect and is never switched off. A browser test that asserts too early passes for the wrong reason.

### Verifying a profile

```bash
python scripts/verify_defects.py
```

Proves each enabled defect actually reproduces, and that a disabled one does not. Run it after changing a
profile — a defect that cannot be triggered is worthless, and one that fires when it should not is worse.

---

## Layout

```
backend/app/
  main.py       HTTP API
  worker.py     the five-stage pipeline
  events.py     RabbitMQ publishing, envelope, DLQ topology
  outputs.py    golden generation and output perturbation
  chaos.py      the single seeded random stream
  db.py         pool, schema DDL
  config.py     every env var, in one place
frontend/
  app/          Next.js app router - job list, submit form, job detail
  lib/          API client, UI defect flags
scripts/
  smoke.py           liveness check
  verify_defects.py  proves a defect profile is armed
  add_defects.py     record of how d10-d17 were introduced
```

Stable `data-testid` attributes are on every interactive element in the UI, so browser tests exercise
behaviour rather than selector archaeology.

---

## Not in scope

No auth, no migrations framework, no observability stack, no deployment. Don't file issues about them, and
don't spend your time there.
