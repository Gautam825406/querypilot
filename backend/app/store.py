"""Audit store (SQLite) + result cache (see app/cache.py).

Tables: runs, insights, feedback, limits, users, workspaces, insight_runs,
eval_runs. Migrations are additive (new tables / new columns only) so an
existing audit DB keeps working.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path

from .cache import cache_get, cache_invalidate, cache_key, cache_put  # noqa: F401  (re-exported for callers)
from .config import settings

_cache: dict[str, tuple[float, dict]] = {}


def _migrate(con: sqlite3.Connection) -> None:
    """Additive migrations: new columns only, guarded by PRAGMA checks."""
    existing = {r[1] for r in con.execute("PRAGMA table_info(insights)").fetchall()}
    for col, ddl in (("owner_email", "TEXT"), ("last_run_at", "REAL"),
                     ("next_run_at", "REAL")):
        if col not in existing:
            con.execute(f"ALTER TABLE insights ADD COLUMN {col} {ddl}")


def _conn():
    Path(settings.audit_db_path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(settings.audit_db_path)
    con.execute("""CREATE TABLE IF NOT EXISTS runs (
        id TEXT PRIMARY KEY, question TEXT, workspace TEXT, status TEXT, intent TEXT,
        metric TEXT, sql TEXT, layer_version TEXT, validation TEXT, attempts TEXT,
        columns_json TEXT, rows_json TEXT, row_count INTEGER, duration_ms INTEGER,
        tokens INTEGER, cost REAL, explanation TEXT, chart_json TEXT, created_at REAL)""")
    con.execute("""CREATE TABLE IF NOT EXISTS insights (
        id TEXT PRIMARY KEY, run_id TEXT, question TEXT, sql TEXT, cadence TEXT,
        threshold REAL, status TEXT, last_value REAL, created_at REAL)""")
    con.execute("""CREATE TABLE IF NOT EXISTS feedback (
        id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, rating TEXT,
        correction TEXT, created_at REAL)""")
    con.execute("""CREATE TABLE IF NOT EXISTS limits (
        id INTEGER PRIMARY KEY, row_limit INTEGER, timeout_seconds INTEGER, max_cost_per_query REAL)""")
    con.execute("""CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY, email TEXT UNIQUE, name TEXT, password_hash TEXT,
        role TEXT, workspace TEXT, created_at REAL)""")
    con.execute("""CREATE TABLE IF NOT EXISTS workspaces (
        id TEXT PRIMARY KEY, name TEXT, owner_email TEXT, created_at REAL)""")
    con.execute("""CREATE TABLE IF NOT EXISTS insight_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, insight_id TEXT, value REAL,
        breached INTEGER, notified INTEGER, delivery TEXT, created_at REAL)""")
    con.execute("""CREATE TABLE IF NOT EXISTS eval_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, config TEXT, result_json TEXT, created_at REAL)""")
    _migrate(con)
    con.commit()
    return con


def save_run(run: dict) -> None:
    con = _conn()
    con.execute("INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (run["id"], run["question"], run.get("workspace", "demo"), run["status"],
                 run.get("intent"), run.get("metric"), run.get("sql"), run.get("layer_version"),
                 json.dumps(run.get("validation", {})), json.dumps(run.get("attempts", [])),
                 json.dumps(run.get("columns", [])), json.dumps(run.get("rows", [])),
                 run.get("row_count", 0), run.get("duration_ms", 0), run.get("tokens", 0),
                 run.get("cost", 0.0), run.get("explanation"), json.dumps(run.get("chart_spec", {})),
                 time.time()))
    con.commit()
    con.close()


def get_run(run_id: str):
    con = _conn()
    con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    con.close()
    return dict(row) if row else None


def list_runs(limit: int = 50):
    con = _conn()
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    con.close()
    return [dict(r) for r in rows]


def save_insight(ins: dict) -> None:
    con = _conn()
    con.execute("INSERT OR REPLACE INTO insights "
                "(id, run_id, question, sql, cadence, threshold, status, last_value, created_at, "
                " owner_email, last_run_at, next_run_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (ins["id"], ins.get("run_id"), ins.get("question"), ins.get("sql"),
                 ins.get("cadence", "daily"), ins.get("threshold"), ins.get("status", "active"),
                 ins.get("last_value"), time.time(), ins.get("owner_email"),
                 ins.get("last_run_at"), ins.get("next_run_at")))
    con.commit()
    con.close()


def list_insights():
    con = _conn()
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT * FROM insights ORDER BY created_at DESC").fetchall()
    con.close()
    return [dict(r) for r in rows]


def update_insight(ins_id: str, fields: dict):
    con = _conn()
    for k, v in fields.items():
        con.execute(f"UPDATE insights SET {k}=? WHERE id=?", (v, ins_id))
    con.commit()
    con.close()


def delete_insight(ins_id: str):
    con = _conn()
    con.execute("DELETE FROM insights WHERE id=?", (ins_id,))
    con.commit()
    con.close()


def save_feedback(run_id: str, rating: str, correction: str | None):
    con = _conn()
    con.execute("INSERT INTO feedback (run_id, rating, correction, created_at) VALUES (?,?,?,?)",
                (run_id, rating, correction, time.time()))
    con.commit()
    con.close()


def get_limits():
    con = _conn()
    row = con.execute("SELECT * FROM limits WHERE id=1").fetchone()
    con.close()
    if row:
        return {"row_limit": row[1], "timeout_seconds": row[2], "max_cost_per_query": row[3]}
    return {"row_limit": settings.row_limit, "timeout_seconds": settings.timeout_seconds,
            "max_cost_per_query": 0.50}


def set_limits(row_limit=None, timeout_seconds=None, max_cost_per_query=None):
    cur = get_limits()
    cur.update({k: v for k, v in
                {"row_limit": row_limit, "timeout_seconds": timeout_seconds,
                 "max_cost_per_query": max_cost_per_query}.items() if v is not None})
    con = _conn()
    con.execute("INSERT OR REPLACE INTO limits VALUES (1,?,?,?)",
                (cur["row_limit"], cur["timeout_seconds"], cur["max_cost_per_query"]))
    con.commit()
    con.close()
    return cur


# ---- Users & workspaces (FR-18) ----

def create_user(email: str, name: str, password_hash: str, role: str,
                workspace: str = "demo") -> dict:
    import uuid
    user = {"id": str(uuid.uuid4())[:8], "email": email, "name": name,
            "password_hash": password_hash, "role": role, "workspace": workspace}
    con = _conn()
    con.execute("INSERT INTO users VALUES (?,?,?,?,?,?,?)",
                (user["id"], email, name, password_hash, role, workspace, time.time()))
    con.execute("INSERT OR IGNORE INTO workspaces VALUES (?,?,?,?)",
                (workspace, workspace, email, time.time()))
    con.commit()
    con.close()
    return {k: v for k, v in user.items() if k != "password_hash"}


def get_user_by_email(email: str):
    con = _conn()
    con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    con.close()
    return dict(row) if row else None


def ensure_demo_user() -> dict:
    """Seed the demo logins on first boot. Idempotent; never resets an
    existing password.
      demo@querypilot.local / demo123  — business_user (ask, save insights)
      admin@querypilot.local / admin123 — admin (evals, admin limits)
    """
    from .auth import hash_password  # local import: auth imports store
    existing = get_user_by_email("demo@querypilot.local")
    if not existing:
        create_user("demo@querypilot.local", "Demo User",
                    hash_password("demo123"), "business_user", "demo")
    if not get_user_by_email("admin@querypilot.local"):
        create_user("admin@querypilot.local", "Demo Admin",
                    hash_password("admin123"), "admin", "demo")
    user = get_user_by_email("demo@querypilot.local")
    return {k: v for k, v in user.items() if k != "password_hash"}


# ---- Scheduled insights: runs & due scan (FR-11) ----

def get_insight(ins_id: str):
    con = _conn()
    con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM insights WHERE id=?", (ins_id,)).fetchone()
    con.close()
    return dict(row) if row else None


def list_due_insights(now: float):
    """Active insights whose next run is due (never-run insights are due)."""
    con = _conn()
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT * FROM insights WHERE status='active' "
        "AND (next_run_at IS NULL OR next_run_at <= ?) ORDER BY created_at", (now,)).fetchall()
    con.close()
    return [dict(r) for r in rows]


def record_insight_run(insight_id: str, value, breached: bool,
                       notified: bool, delivery: dict) -> None:
    con = _conn()
    con.execute("INSERT INTO insight_runs (insight_id, value, breached, notified, delivery, created_at) "
                "VALUES (?,?,?,?,?,?)",
                (insight_id, value, 1 if breached else 0, 1 if notified else 0,
                 json.dumps(delivery), time.time()))
    con.commit()
    con.close()


def list_insight_runs(insight_id: str, limit: int = 20):
    con = _conn()
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT * FROM insight_runs WHERE insight_id=? ORDER BY created_at DESC LIMIT ?",
                       (insight_id, limit)).fetchall()
    con.close()
    return [dict(r) for r in rows]


def save_eval_run(config: str, result: dict) -> int:
    con = _conn()
    cur = con.execute("INSERT INTO eval_runs (config, result_json, created_at) VALUES (?,?,?)",
                      (config, json.dumps(result), time.time()))
    con.commit()
    con.close()
    return cur.lastrowid
