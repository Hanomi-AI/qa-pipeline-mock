"""Golden generation, and the perturbations applied on the way out.

The golden for an input is deterministic from its input_ref, so goldens can be
regenerated at any time and never drift. A completed job's output is the golden
with whatever misbehaviour is currently switched on applied to it.
"""
import hashlib
from copy import deepcopy

from . import chaos, config

TYPES = ["scalar", "ratio", "bounded"]


def _seed_of(input_ref: str) -> int:
    return int(hashlib.sha256(input_ref.encode()).hexdigest()[:12], 16)


def golden(input_ref: str, input_type: str) -> dict:
    """Deterministic expected output. Pure function of (input_ref, input_type)."""
    s = _seed_of(input_ref)
    n = 8 + (s % 5)                      # 8..12 measurements
    measurements = []
    for i in range(1, n + 1):
        h = (s >> (i % 24)) ^ (i * 2654435761)
        mtype = TYPES[h % 3]
        nominal = round(1.0 + (h % 500000) / 1000.0, 3)
        if mtype == "bounded":
            tol_plus, tol_minus = 0.01, 0.0     # asymmetric on purpose
        elif mtype == "ratio":
            tol_plus = tol_minus = 0.05
        else:
            tol_plus = tol_minus = 0.1
        measurements.append({
            "id": f"M-{i:03d}",
            "type": mtype,
            "nominal": nominal,
            "value": nominal,
            "tol_plus": tol_plus,
            "tol_minus": tol_minus,
        })
    return {
        "input_type": input_type,
        "schema_version": "1.2",
        "units": "mm",
        "measurements": measurements,
        "metadata": {"stages": [], "warnings": []},
    }


def realise(job_id: str, input_ref: str, input_type: str,
            stage_times: list[dict]) -> dict:
    """The output the service actually returns: golden + active misbehaviour."""
    out = deepcopy(golden(input_ref, input_type))
    out["job_id"] = job_id
    out["metadata"]["stages"] = stage_times

    ms = out["measurements"]

    # DRIFT_PCT - nudge a value just past its tolerance
    for m in ms:
        if chaos.hit(config.DRIFT_PCT):
            m["value"] = round(m["nominal"] + m["tol_plus"] + 0.002, 5)

    # UNIT_FLIP - declare inches, keep reporting millimetres
    if chaos.hit(config.UNIT_FLIP):
        out["units"] = "in"

    # SCHEMA_DRIFT - a renamed field under a bumped version
    if chaos.hit(config.SCHEMA_DRIFT):
        out["schema_version"] = "1.3"
        for m in ms:
            m["expected"] = m.pop("nominal")

    # ---- graded defect profile (see README) ----

    # d1: exactly one tick past the limit, for one input_type only.
    #     A comparison that rounds the limit away will pass this.
    if config.defect("d1") and input_type == "legacy":
        for m in ms:
            if m["id"] == "M-007" and "nominal" in m:
                m["value"] = round(m["nominal"] + m["tol_plus"] + 1e-4, 6)

    # d2: units say one thing, values are the other
    if config.defect("d2") and input_type == "raw" and chaos.hit(1 / 6):
        out["units"] = "in"

    # d3: a measurement silently absent. Iterating the OUTPUT misses this;
    #     iterating the GOLDEN catches it.
    if config.defect("d3") and input_type == "structured":
        out["measurements"] = [m for m in ms if m["id"] != "M-005"]

    # d4: duplicate id with conflicting values. match-by-id must detect the
    #     collision rather than silently taking first or last.
    if config.defect("d4") and input_type == "compressed":
        for m in list(out["measurements"]):
            if m["id"] == "M-003":
                dup = deepcopy(m)
                dup["value"] = round(m["value"] + m["tol_plus"] * 3, 5)
                out["measurements"].append(dup)
                break

    # d16: numbers as strings, for one input_type only. float("42.53") == 42.53,
    # so a coercing comparator sails past a genuine schema violation.
    if config.defect("d16") and input_type == "raw":
        for m in out["measurements"]:
            for k in ("value", "nominal", "tol_plus", "tol_minus"):
                if k in m and isinstance(m[k], (int, float)):
                    m[k] = str(m[k])

    return out
