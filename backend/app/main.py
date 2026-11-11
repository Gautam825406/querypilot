"""QueryPilot API — FastAPI. Every ask runs the real LangGraph pipeline.

Auth (FR-18): JWT with roles admin / analyst / business_user.
- /api/ask works WITHOUT a token for the demo workspace (anonymous = demo),
  so the public demo never breaks.
- An authenticated user is scoped to their own workspace: their workspace
  overrides any workspace in the request.
- Mutating/admin endpoints require a token with the right role.
"""
from __future__ import annotations

import uuid

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .auth import create_token, decode_token, hash_password, verify_password
from .cache import cache_backend_name, cache_invalidate
from .config import settings
from .insights import execute_insight, list_runs as list_insight_runs
from .planner import _ensure_demo_db, parse_question, run_ask
from .semantic import SCHEMA, layer
from .store import (create_user, delete_insight, ensure_demo_user, get_dataset,
                    get_insight, get_limits, get_run, get_user_by_email,
                    list_datasets, list_insights, list_runs, save_eval_run,
                    save_feedback, save_insight, set_limits, update_insight)
from .uploads import MAX_FILE_BYTES, UploadError, delete_dataset, upload_dataset
from .validator import ValidationError, validate_sql

app = FastAPI(title="QueryPilot", version="1.0.0",
              description="Trusted text-to-SQL analytics agent with a semantic layer. Sample data only.")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.on_event("startup")
def startup():
    _ensure_demo_db()
    ensure_demo_user()


# ---- Auth dependencies ----

def get_current_user(authorization: str | None = Header(default=None)):
    """Optional auth: returns the user dict, or None for anonymous callers."""
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    payload = decode_token(authorization.split(" ", 1)[1].strip())
    if not payload:
        return None
    user = get_user_by_email(payload.get("sub", ""))
    if not user:
        return None
    return {k: v for k, v in user.items() if k != "password_hash"}


def require_user(user: dict | None = Depends(get_current_user)) -> dict:
    if not user:
        raise HTTPException(401, "Sign in required for this action.")
    return user


def require_role(*roles: str):
    def checker(user: dict = Depends(require_user)) -> dict:
        if user.get("role") not in roles:
            raise HTTPException(403, f"Requires role: {' or '.join(roles)} (you are {user.get('role')}).")
        return user
    return checker


def _workspace_for(req_workspace: str, user: dict | None) -> str:
    """Authenticated users are scoped to their own workspace."""
    return user["workspace"] if user else (req_workspace or "demo")


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


class RegisterRequest(BaseModel):
    email: str
    name: str = ""
    password: str
    role: str = "business_user"


class LoginRequest(BaseModel):
    email: str
    password: str


def _public(run: dict) -> dict:
    return run


@app.get("/api/health")
@app.get("/health")
@app.get("/")
def health():
    return {"status": "ok", "service": "querypilot", "llm_mode": settings.llm_mode,
            "layer_version": layer.version, "sample_data": True,
            "dialect": settings.dialect,
            "database_configured": bool(settings.database_url),
            "cache_backend": cache_backend_name(),
            "scheduler": "celery+redis" if settings.scheduler_enabled else "disabled (no REDIS_URL)",
            "auth": "jwt",
            "message": "QueryPilot is up. All data is labelled sample data."}


# ---- Auth ----

@app.post("/api/auth/register")
def register(req: RegisterRequest, user: dict | None = Depends(get_current_user)):
    if get_user_by_email(req.email):
        raise HTTPException(400, "An account with that email already exists.")
    # Only an admin may create admin/analyst accounts; everyone else registers
    # as business_user regardless of the requested role.
    role = req.role if (user and user.get("role") == "admin" and req.role in ("admin", "analyst", "business_user")) else "business_user"
    created = create_user(req.email, req.name or req.email.split("@")[0],
                          hash_password(req.password), role, "demo")
    return {**created, "access_token": create_token(created), "token_type": "bearer"}


@app.post("/api/auth/login")
def login(req: LoginRequest):
    stored = get_user_by_email(req.email)
    if not stored or not verify_password(req.password, stored.get("password_hash", "")):
        raise HTTPException(401, "Invalid email or password.")
    user = {k: v for k, v in stored.items() if k != "password_hash"}
    return {**user, "access_token": create_token(user), "token_type": "bearer"}


@app.get("/api/auth/me")
def me(user: dict = Depends(require_user)):
    return user


# ---- Ask ----

@app.post("/api/ask")
def ask(req: AskRequest, user: dict | None = Depends(get_current_user)):
    return _public(run_ask(req.question, _workspace_for(req.workspace, user)))


@app.post("/api/ask/{run_id}/follow-up")
def follow_up(run_id: str, req: FollowUpRequest, user: dict | None = Depends(get_current_user)):
    prior = get_run(run_id)
    if not prior:
        raise HTTPException(404, "Run not found")
    prior_ctx = {"metric": prior.get("metric"), "dimension": None}
    return _public(run_ask(req.question, _workspace_for(prior.get("workspace", "demo"), user),
                           prior=prior_ctx))


@app.get("/api/runs/{run_id}")
def get_run_endpoint(run_id: str):
    run = get_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    import json
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
def models(workspace: str = "demo", user: dict | None = Depends(get_current_user)):
    ws = _workspace_for(workspace, user)
    uploads = list_datasets(ws)
    schema = {**SCHEMA,
              **{ds["table_name"]: {c["name"]: c["type"].upper() for c in ds["columns"]}
                 for ds in uploads}}
    return {"layer_version": layer.version, "models": list(layer.models.values()),
            "dimensions": list(layer.dimensions.values()), "joins": layer.joins,
            "schema": schema, "uploaded_datasets": uploads,
            "pii_columns": ["customers.email", "customers.phone"]}


# ---- CSV uploads (DuckDB demo database only) ----

@app.post("/api/data/upload")
async def upload_csv(file: UploadFile = File(...),
                     user: dict | None = Depends(get_current_user)):
    """Upload one .csv file (max 5 MB, 100k rows, 50 columns) and store it as
    a real DuckDB table that /api/ask can query. Auth optional like /api/ask:
    anonymous callers upload into the demo workspace."""
    workspace = _workspace_for("demo", user)
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(400, "Only .csv files can be uploaded.")
    data = await file.read()
    if len(data) > MAX_FILE_BYTES:
        raise HTTPException(400, f"File is {len(data) / 1024 / 1024:.1f} MB — the limit is 5 MB. "
                                 "Split the file or upload a smaller sample.")
    try:
        ds = upload_dataset(data, file.filename, workspace)
    except UploadError as e:
        raise HTTPException(400, str(e))
    # Uploaded tables change what questions can answer: drop cached answers.
    cache_invalidate()
    return ds


@app.get("/api/data/datasets")
def list_data(workspace: str = "demo", user: dict | None = Depends(get_current_user)):
    ws = _workspace_for(workspace, user)
    return {"datasets": list_datasets(ws)}


@app.delete("/api/data/datasets/{dataset_id}")
def delete_data(dataset_id: str, user: dict | None = Depends(get_current_user)):
    workspace = _workspace_for("demo", user)
    ds = get_dataset(dataset_id)
    if not ds or ds.get("workspace") != workspace:
        raise HTTPException(404, "Dataset not found")
    delete_dataset(dataset_id)
    cache_invalidate()
    return {"deleted": dataset_id}


# ---- Scheduled insights ----

@app.get("/api/insights")
def list_ins():
    return {"insights": list_insights()}


@app.post("/api/insights")
def create_ins(req: InsightRequest, user: dict = Depends(require_user)):
    ins = {"id": str(uuid.uuid4())[:8], "run_id": req.run_id, "question": req.question,
           "sql": req.sql, "cadence": req.cadence, "threshold": req.threshold,
           "status": "active", "last_value": None, "owner_email": user["email"],
           "last_run_at": None, "next_run_at": None}
    if req.sql:
        try:
            validate_sql(req.sql)
        except ValidationError as e:
            raise HTTPException(400, f"Insight SQL failed validation ({e.rule}): {e.message}")
    save_insight(ins)
    return ins


@app.patch("/api/insights/{ins_id}")
def patch_ins(ins_id: str, body: dict, user: dict = Depends(require_user)):
    ins = get_insight(ins_id)
    if not ins:
        raise HTTPException(404, "Insight not found")
    if user.get("role") != "admin" and ins.get("owner_email") not in (None, user["email"]):
        raise HTTPException(403, "Only the insight owner or an admin can change it.")
    allowed = {k: v for k, v in body.items() if k in ("cadence", "threshold", "status", "last_value")}
    update_insight(ins_id, allowed)
    return {"id": ins_id, **allowed}


@app.delete("/api/insights/{ins_id}")
def delete_ins(ins_id: str, user: dict = Depends(require_user)):
    ins = get_insight(ins_id)
    if not ins:
        raise HTTPException(404, "Insight not found")
    if user.get("role") != "admin" and ins.get("owner_email") not in (None, user["email"]):
        raise HTTPException(403, "Only the insight owner or an admin can delete it.")
    delete_insight(ins_id)
    return {"deleted": ins_id}


@app.post("/api/insights/{ins_id}/run-now")
def run_insight_now(ins_id: str, user: dict = Depends(require_user)):
    """Synchronous insight run: re-validates, executes, checks the threshold
    and notifies on breach. Works without Redis/Celery."""
    ins = get_insight(ins_id)
    if not ins:
        raise HTTPException(404, "Insight not found")
    if user.get("role") != "admin" and ins.get("owner_email") not in (None, user["email"]):
        raise HTTPException(403, "Only the insight owner or an admin can run it.")
    return execute_insight(ins_id)


@app.get("/api/insights/{ins_id}/runs")
def insight_runs(ins_id: str):
    return {"insight_id": ins_id, "runs": list_insight_runs(ins_id)}


@app.post("/api/feedback")
def feedback(req: FeedbackRequest):
    save_feedback(req.run_id, req.rating, req.correction)
    return {"ok": True, "run_id": req.run_id, "rating": req.rating}


@app.get("/api/admin/limits")
def get_lim(user: dict = Depends(require_role("admin"))):
    return get_limits()


@app.patch("/api/admin/limits")
def patch_lim(req: LimitsRequest, user: dict = Depends(require_role("admin"))):
    return set_limits(req.row_limit, req.timeout_seconds, req.max_cost_per_query)


@app.post("/api/evals/run")
def run_evals(user: dict = Depends(require_role("admin"))):
    from evals.run_eval import main as eval_main
    result = eval_main()
    save_eval_run("full", result)
    return result
