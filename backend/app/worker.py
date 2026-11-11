"""Celery worker (FR-11, FR-17): scheduled insights + evaluation runs.

Run locally / on Render as a background worker:
    celery -A app.worker.celery_app worker --loglevel=info
    celery -A app.worker.celery_app beat --loglevel=info   (cadence scanner)

The broker is REDIS_URL. Without it the worker module still imports, but the
tasks refuse to run and the API/README say the scheduler is disabled — the
synchronous POST /api/insights/{id}/run-now endpoint works without Redis.
"""
from __future__ import annotations

import logging
import time

from celery import Celery

from .config import settings

logger = logging.getLogger("querypilot.worker")

celery_app = Celery(
    "querypilot",
    broker=settings.redis_url or "memory://",
    backend=(settings.redis_url or "cache+memory://"),
)
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    beat_schedule={
        "scan-due-insights": {
            "task": "app.worker.scan_due_insights",
            "schedule": 60.0,  # every minute; due-ness is decided per insight
        },
    },
)


def _require_broker():
    if not settings.redis_url:
        raise RuntimeError(
            "Celery broker not configured: set REDIS_URL to run scheduled tasks. "
            "Use POST /api/insights/{id}/run-now for a synchronous run instead.")


@celery_app.task(name="app.worker.run_scheduled_insight")
def run_scheduled_insight(insight_id: str) -> dict:
    _require_broker()
    from .insights import execute_insight
    return execute_insight(insight_id)


@celery_app.task(name="app.worker.scan_due_insights")
def scan_due_insights() -> dict:
    """Every minute: enqueue (or, without a broker, run inline) due insights."""
    _require_broker()
    from .store import list_due_insights
    due = list_due_insights(time.time())
    enqueued = []
    for ins in due:
        run_scheduled_insight.delay(ins["id"])
        enqueued.append(ins["id"])
    logger.info("scan_due_insights: %d due insight(s) enqueued", len(enqueued))
    return {"due": len(due), "enqueued": enqueued}


@celery_app.task(name="app.worker.run_eval_task")
def run_eval_task() -> dict:
    _require_broker()
    from evals.run_eval import main as eval_main
    return eval_main()
