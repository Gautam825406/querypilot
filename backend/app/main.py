"""QueryPilot API — FastAPI. Every ask runs the real LangGraph pipeline."""
from __future__ import annotations

import uuid

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .config import settings
from .planner import _ensure_demo_db, parse_question, run_ask
from .semantic import SCHEMA, layer
from .store import (delete_insight, get_limits, get_run, list_insights, list_runs,
                    save_feedback, save_insight, set_limits, update_insight)
from .validator import ValidationError, validate_sql

app = FastAPI(title="QueryPilot", version="1.0.0",
              description="Trusted text-to-SQL analytics agent with a semantic layer. Sample data only.")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.on_event("startup")
def startup():
    _ensure_demo_db()


class AskRequest(BaseModel):
    question: str
    workspace: str = "demo"


class FollowUpRequest(BaseModel):
    question: str


class InsightRequest(BaseModel):
    run_id: str | None = None
    question: str = ""
    sql: str = ""
    cadence: str = "daily"
    threshold: float | None = None


class FeedbackRequest(BaseModel):
    run_id: str
    rating: str
    correction: str | None = None


class LimitsRequest(BaseModel):
    row_limit: int | None = None
    timeout_seconds: int | None = None
    max_cost_per_query: float | None = None


def _public(run: dict) -> dict:
    return run


@app.get("/api/health")
@app.get("/health")
@app.get("/")
def health():
    return {"status": "ok", "service": "querypilot", "llm_mode": settings.llm_mode,
            "layer_version": layer.version, "sample_data": True,
            "message": "QueryPilot is up. All data is labelled sample data."}


@app.post("/api/ask")
def ask(req: AskRequest):
    return _public(run_ask(req.question, req.workspace))


@app.post("/api/ask/{run_id}/follow-up")
def follow_up(run_id: str, req: FollowUpRequest):
    prior = get_run(run_id)
    if not prior:
        raise HTTPException(404, "Run not found")
    import json
    prior_ctx = {"metric": prior.get("metric"),
                 "dimension": None}
    return _public(run_ask(req.question, prior.get("workspace", "demo"), prior=prior_ctx))


@app.get("/api/runs/{run_id}")
def get_run_endpoint(run_id: str):
    run = get_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    import json
    for k in ("validation", "attempts", "columns_json", "rows_json", "chart_json"):
        pass
    run["validation"] = json.loads(run["validation"] or "{}")
    run["attempts"] = json.loads(run["attempts"] or "[]")
    run["columns"] = json.loads(run["columns_json"] or "[]")
    run["rows"] = json.loads(run["rows_json"] or "[]")
    run["chart_spec"] = json.loads(run["chart_json"] or "{}")
    return run


@app.get("/api/history")
def history(limit: int = 50):
    import json
    out = []
    for r in list_runs(limit):
        r["validation"] = json.loads(r["validation"] or "{}")
        r["attempts"] = json.loads(r["attempts"] or "[]")
        r["columns"] = json.loads(r["columns_json"] or "[]")
        r["rows"] = json.loads(r["rows_json"] or "[]")
        r["chart_spec"] = json.loads(r["chart_json"] or "{}")
        out.append(r)
    return {"runs": out}


@app.get("/api/semantic/metrics")
def metrics():
    return {"layer_version": layer.version, "metrics": list(layer.metrics.values())}


@app.get("/api/semantic/models")
def models():
    return {"layer_version": layer.version, "models": list(layer.models.values()),
            "dimensions": list(layer.dimensions.values()), "joins": layer.joins,
            "schema": SCHEMA, "pii_columns": ["customers.email", "customers.phone"]}


@app.get("/api/insights")
def list_ins():
    return {"insights": list_insights()}


@app.post("/api/insights")
def create_ins(req: InsightRequest):
    ins = {"id": str(uuid.uuid4())[:8], "run_id": req.run_id, "question": req.question,
           "sql": req.sql, "cadence": req.cadence, "threshold": req.threshold,
           "status": "active", "last_value": None}
    if req.sql:
        try:
            validate_sql(req.sql)
        except ValidationError as e:
            raise HTTPException(400, f"Insight SQL failed validation ({e.rule}): {e.message}")
    save_insight(ins)
    return ins


@app.patch("/api/insights/{ins_id}")
def patch_ins(ins_id: str, body: dict):
    allowed = {k: v for k, v in body.items() if k in ("cadence", "threshold", "status", "last_value")}
    update_insight(ins_id, allowed)
    return {"id": ins_id, **allowed}


@app.delete("/api/insights/{ins_id}")
def delete_ins(ins_id: str):
    delete_insight(ins_id)
    return {"deleted": ins_id}


@app.post("/api/feedback")
def feedback(req: FeedbackRequest):
    save_feedback(req.run_id, req.rating, req.correction)
    return {"ok": True, "run_id": req.run_id, "rating": req.rating}


@app.get("/api/admin/limits")
def get_lim():
    return get_limits()


@app.patch("/api/admin/limits")
def patch_lim(req: LimitsRequest):
    return set_limits(req.row_limit, req.timeout_seconds, req.max_cost_per_query)


@app.post("/api/evals/run")
def run_evals():
    from evals.run_eval import main as eval_main
    return eval_main()
