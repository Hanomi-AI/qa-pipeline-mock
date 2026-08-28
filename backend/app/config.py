"""Configuration. Every knob is an env var with a default, so the service runs
with no configuration at all and misbehaves in interesting ways on request."""
import os


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


def _i(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://pipeline:pipeline@localhost:5432/pipeline")
AMQP_URL = os.getenv("AMQP_URL", "amqp://guest:guest@localhost:5672/")

SEED = _i("SEED", 20260828)
WORKER_COUNT = _i("WORKER_COUNT", 4)

# pipeline misbehaviour
FAIL_RATE = _f("FAIL_RATE", 0.06)
LATENCY_MS = _f("LATENCY_MS", 1.0)
HANG_RATE = _f("HANG_RATE", 0.01)
DRIFT_PCT = _f("DRIFT_PCT", 0.04)
SCHEMA_DRIFT = _f("SCHEMA_DRIFT", 0.02)
UNIT_FLIP = _f("UNIT_FLIP", 0.0)

# event / delivery misbehaviour
DUPLICATE_WEBHOOKS = _f("DUPLICATE_WEBHOOKS", 0.10)
EVENT_DUP_RATE = _f("EVENT_DUP_RATE", 0.08)
EVENT_DROP_RATE = _f("EVENT_DROP_RATE", 0.03)
EVENT_REORDER_RATE = _f("EVENT_REORDER_RATE", 0.10)
POISON_RATE = _f("POISON_RATE", 0.005)

# Graded defect profile. Comma-separated ids, e.g. "d1,d3,d9".
# Empty means a correct service. See README -> Defect profiles.
DEFECTS = {d.strip().lower() for d in os.getenv("DEFECTS", "").split(",") if d.strip()}

STAGES = ["ingest", "parse", "analyze", "compute", "assemble"]
INPUT_TYPES = ["raw", "compressed", "legacy", "structured"]

EXCHANGE = "pipeline.events"
QUEUE = "pipeline.events.q"
DLX = "pipeline.events.dlx"
DLQ = "pipeline.events.dlq"


def defect(name: str) -> bool:
    return name in DEFECTS
