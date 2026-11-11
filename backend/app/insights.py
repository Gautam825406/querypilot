"""Scheduled-insight execution (FR-11).

execute_insight() is the single implementation used by both the Celery worker
and the synchronous POST /api/insights/{id}/run-now endpoint, so a manual run
and a scheduled run behave identically:
  1. Re-validate the saved SQL against the current schema + semantic layer.
     On failure the insight is paused (paused_on_schema_change) and the owner
     is notified — never a silently wrong rerun.
  2. Execute read-only, store the value + run record.
  3. Compare with the threshold; notify ONLY on breach.
"""
from __future__ import annotations

import logging
import time

from .executor import execute_sql
from .notify import notify_insight
from .store import (get_insight, list_insight_runs, record_insight_run,
                    update_insight)
from .validator import ValidationError, validate_sql

logger = logging.getLogger("querypilot.insights")

CADENCE_SECONDS = {"hourly": 3600, "daily": 86400, "weekly": 604800}


def _scalar_value(columns, rows):
    """The insight's headline value: first numeric cell, else the row count."""
    if not rows:
        return 0.0
    v = rows[0][0]
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(v)
    except (TypeError, ValueError):
        return float(len(rows))


def execute_insight(insight_id: str) -> dict:
    ins = get_insight(insight_id)
    if not ins:
        return {"status": "not_found", "insight_id": insight_id}
    if ins.get("status") not in ("active", "paused"):
        return {"status": "skipped", "insight_id": insight_id,
                "reason": f"insight status is {ins.get('status')}"}
    if not ins.get("sql"):
        return {"status": "skipped", "insight_id": insight_id, "reason": "insight has no SQL"}

    # 1. Re-validate against the CURRENT schema/layer.
    try:
        validated = validate_sql(ins["sql"])
    except ValidationError as e:
        update_insight(insight_id, {"status": "paused_on_schema_change"})
        delivery = notify_insight(ins, None, ins.get("threshold"), False,
                                  reason="paused_on_schema_change")
        record_insight_run(insight_id, None, False, bool(delivery), delivery)
        logger.warning("Insight %s paused: re-validation failed (%s): %s",
                       insight_id, e.rule, e.message)
        return {"status": "paused_on_schema_change", "insight_id": insight_id,
                "rule": e.rule, "message": e.message, "delivery": delivery}

    # 2. Execute read-only and store the value.
    now = time.time()
    try:
        columns, rows, _dur = execute_sql(validated["sql"])
    except Exception as e:
        record_insight_run(insight_id, None, False, False, {"error": str(e)})
        return {"status": "failed", "insight_id": insight_id, "error": str(e)}
    value = _scalar_value(columns, rows)
    cadence = CADENCE_SECONDS.get(ins.get("cadence", "daily"), 86400)
    update_insight(insight_id, {"last_value": value, "last_run_at": now,
                                "next_run_at": now + cadence})

    # 3. Threshold: breach = value above the threshold (when one is set).
    threshold = ins.get("threshold")
    breached = threshold is not None and value is not None and value > threshold
    delivery: dict = {}
    notified = False
    if breached:
        delivery = notify_insight(ins, value, threshold, True)
        notified = bool(delivery)
    record_insight_run(insight_id, value, breached, notified, delivery)
    return {"status": "executed", "insight_id": insight_id, "value": value,
            "threshold": threshold, "breached": breached, "notified": notified,
            "delivery": delivery, "last_run_at": now, "next_run_at": now + cadence}


def list_runs(insight_id: str, limit: int = 20):
    return list_insight_runs(insight_id, limit)
