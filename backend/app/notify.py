"""Scheduled-insight notifications (FR-11): generic webhook + Slack webhook.

Alerts fire ONLY on a threshold breach (or a paused-on-schema-change event).
Every delivery attempt is returned (and recorded) — a failed webhook never
crashes the insight run, it is logged as a failed delivery.
"""
from __future__ import annotations

import logging

from .http import post_json

from .config import settings

logger = logging.getLogger("querypilot.notify")


def _post(url: str, payload: dict, label: str) -> dict:
    try:
        r = post_json(url, payload, timeout=10)
        ok = r.status_code < 300
        if not ok:
            logger.warning("Insight notification to %s failed: HTTP %s", label, r.status_code)
        return {"ok": ok, "status": r.status_code}
    except Exception as e:
        logger.warning("Insight notification to %s failed: %s", label, e)
        return {"ok": False, "error": str(e)}


def notify_insight(insight: dict, value, threshold, breached: bool,
                   reason: str = "threshold_breach") -> dict:
    """Notify the configured channels. Returns {channel: delivery-result}."""
    base = {
        "insight_id": insight["id"],
        "question": insight.get("question"),
        "value": value,
        "threshold": threshold,
        "breached": breached,
        "reason": reason,
        "service": "querypilot",
    }
    results: dict = {}
    if settings.insight_webhook_url:
        results["webhook"] = _post(settings.insight_webhook_url, base, "webhook")
    if settings.slack_webhook_url:
        if reason == "paused_on_schema_change":
            text = (f":warning: QueryPilot insight paused — schema changed: "
                    f"“{insight.get('question')}” failed re-validation and was paused.")
        elif breached:
            text = (f":chart_with_upwards_trend: QueryPilot alert: “{insight.get('question')}” "
                    f"is now {value} (threshold {threshold}).")
        else:
            text = (f"QueryPilot insight update: “{insight.get('question')}” = {value}.")
        results["slack"] = _post(settings.slack_webhook_url, {"text": text}, "slack")
    return results
