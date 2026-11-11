"""Audit store (SQLite) + in-memory cache keyed by hash(sql + layer_version), TTL."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path

from .config import settings

_cache: dict[str, tuple[float, dict]] = {}


def cache_key(sql: str, layer_version: str) -> str:
    return hashlib.sha256(f"{sql}|{layer_version}".encode()).hexdigest()[:16]


def cache_get(key: str):
    entry = _cache.get(key)
    if not entry:
        return None
    ts, value = entry
    if time.time() - ts > settings.cache_ttl_seconds:
        del _cache[key]
        return None
    return value


def cache_put(key: str, value: dict) -> None:
    _cache[key] = (time.time(), value)


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
    con.execute("INSERT OR REPLACE INTO insights VALUES (?,?,?,?,?,?,?,?,?)",
                (ins["id"], ins.get("run_id"), ins.get("question"), ins.get("sql"),
                 ins.get("cadence", "daily"), ins.get("threshold"), ins.get("status", "active"),
                 ins.get("last_value"), time.time()))
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
