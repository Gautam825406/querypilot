"""Tests for the full-PRD features (items 1-5, 8 of the build task).

- Postgres-dialect validation + mocked-engine read-only execution
- Redis-fallback cache behaviour
- JWT auth: login, wrong-password rejection, role enforcement on admin endpoints
- Insight run-now + threshold/breach logic
- Golden set size 50

Run: python -m pytest backend/tests -q (from repo root).
These tests never need a live Postgres or Redis: the Postgres executor test
uses a fake engine, and the cache tests point at a closed port.
"""
import json
import sys
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.executor as executor  # noqa: E402
import app.insights as insights  # noqa: E402
from app.cache import (MemoryCache, RedisCache, cache_backend_name,  # noqa: E402
                       cache_key, get_cache, reset_cache_singleton)
from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402
from app.store import delete_insight, update_insight  # noqa: E402
from app.validator import ValidationError, validate_sql  # noqa: E402

GOLDEN_PATH = Path(__file__).resolve().parent.parent / "evals" / "golden.json"

DEMO_EMAIL = "demo@querypilot.local"
DEMO_PASSWORD = "demo123"
ADMIN_EMAIL = "admin@querypilot.local"
ADMIN_PASSWORD = "admin123"

REVENUE_SQL = ("SELECT SUM(amount) AS total_revenue FROM orders "
               "WHERE status = 'completed'")


# ---------- 1. Postgres-dialect validation & read-only execution ----------

def test_validator_postgres_dialect():
    out = validate_sql("SELECT region, SUM(amount) AS total_revenue FROM orders GROUP BY region",
                       dialect="postgres")
    assert out["ok"] is True
    assert out["dialect"] == "postgres"
    # Postgres-specific syntax must pass under the postgres dialect.
    out = validate_sql("SELECT DATE_TRUNC('month', order_date) AS month, "
                       "SUM(amount) AS total_revenue FROM orders GROUP BY 1",
                       dialect="postgres")
    assert out["ok"] is True


def test_validator_postgres_dialect_blocks_writes():
    with pytest.raises(ValidationError) as e:
        validate_sql("DELETE FROM orders WHERE id = 1", dialect="postgres")
    assert e.value.rule == "read_only"


def test_validator_default_dialect_is_duckdb():
    out = validate_sql("SELECT region FROM orders")
    assert out["dialect"] == "duckdb"


class _FakeResult:
    def keys(self):
        return ["total_revenue"]

    def fetchmany(self, n):
        return [(517171.84,)]


class _FakeConn:
    def __init__(self):
        self.calls = []

    def exec_driver_sql(self, sql):
        self.calls.append(sql)

    def execute(self, stmt):
        self.calls.append(str(stmt))
        return _FakeResult()


class _FakeCtx:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self.conn

    def __exit__(self, *exc):
        return False


class _FakeEngine:
    def __init__(self):
        self.conn = _FakeConn()

    def begin(self):
        return _FakeCtx(self.conn)


def test_postgres_executor_uses_read_only_transaction(monkeypatch):
    """No live Postgres needed: a fake engine records the exact statements
    the executor issues, proving the READ ONLY transaction + timeout run
    before the query itself."""
    fake = _FakeEngine()
    monkeypatch.setattr(settings, "database_url",
                        "postgresql://qp:qp@localhost:5432/querypilot")
    monkeypatch.setattr(executor, "create_engine", lambda *a, **k: fake)
    executor.reset_engine_cache()
    try:
        cols, rows, _ms = executor.execute_sql(REVENUE_SQL)
        assert cols == ["total_revenue"]
        assert rows == [[517171.84]]
        assert fake.conn.calls[0] == "SET TRANSACTION READ ONLY"
        assert fake.conn.calls[1].startswith("SET LOCAL statement_timeout")
        assert "SUM(amount)" in fake.conn.calls[2]
    finally:
        executor.reset_engine_cache()


# ---------- 2. Cache behaviour ----------

def test_cache_key_scoped_and_versioned():
    k1 = cache_key("SELECT 1", "1.0", "demo")
    k2 = cache_key("SELECT 1", "1.0", "demo")
    k3 = cache_key("SELECT 1", "1.1", "demo")
    k4 = cache_key("SELECT 1", "1.0", "other-workspace")
    assert k1 == k2
    assert k1 != k3 and k1 != k4


def test_memory_cache_roundtrip_and_ttl():
    c = MemoryCache()
    c.put("k", {"rows": [[1]]})
    assert c.get("k") == {"rows": [[1]]}
    assert c.get("missing") is None
    # TTL: entries expire (cache TTL is 300s by default; use a direct
    # expiry by overwriting the stored timestamp).
    c._data["old"] = (time.time() - 1000, {"rows": []})
    assert c.get("old") is None


def test_cache_falls_back_to_memory_when_redis_unreachable(monkeypatch):
    monkeypatch.setattr(settings, "redis_url", "redis://127.0.0.1:6399/0")
    reset_cache_singleton()
    try:
        cache = get_cache()
        assert isinstance(cache, MemoryCache)
        assert cache_backend_name() == "memory"
        cache.put("k", {"v": 1})
        assert cache.get("k") == {"v": 1}
    finally:
        reset_cache_singleton()
        monkeypatch.setattr(settings, "redis_url", "")


def test_redis_cache_degrades_to_memory_on_error():
    rc = RedisCache("redis://127.0.0.1:6399/0")
    rc.put("k", {"v": 2})
    assert rc.get("k") == {"v": 2}


# ---------- 3. JWT auth + roles ----------

@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _login(client, email, password):
    return client.post("/api/auth/login", json={"email": email, "password": password})


def _token(client, email, password):
    r = _login(client, email, password)
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _auth_header(token):
    return {"Authorization": f"Bearer {token}"}


def test_demo_login_and_me(client):
    token = _token(client, DEMO_EMAIL, DEMO_PASSWORD)
    me = client.get("/api/auth/me", headers=_auth_header(token))
    assert me.status_code == 200
    assert me.json()["email"] == DEMO_EMAIL
    assert me.json()["role"] == "business_user"


def test_login_wrong_password_rejected(client):
    r = _login(client, DEMO_EMAIL, "not-the-password")
    assert r.status_code == 401


def test_register_and_login_new_user(client):
    email = f"tester-{uuid.uuid4().hex[:8]}@example.com"
    r = client.post("/api/auth/register",
                    json={"email": email, "name": "Tester", "password": "secret123",
                          "role": "admin"})
    assert r.status_code == 200, r.text
    # Role escalation is refused: a self-registered user is business_user.
    assert r.json()["role"] == "business_user"
    assert r.json()["access_token"]
    r2 = _login(client, email, "secret123")
    assert r2.status_code == 200
    assert r2.json()["role"] == "business_user"


def test_admin_limits_require_admin_role(client):
    # No token -> 401.
    assert client.get("/api/admin/limits").status_code == 401
    # business_user token -> 403.
    demo_token = _token(client, DEMO_EMAIL, DEMO_PASSWORD)
    assert client.get("/api/admin/limits",
                      headers=_auth_header(demo_token)).status_code == 403
    # admin token -> 200.
    admin_token = _token(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    r = client.get("/api/admin/limits", headers=_auth_header(admin_token))
    assert r.status_code == 200
    assert "row_limit" in r.json()


def test_evals_run_requires_admin_role(client):
    assert client.post("/api/evals/run").status_code == 401
    demo_token = _token(client, DEMO_EMAIL, DEMO_PASSWORD)
    assert client.post("/api/evals/run",
                       headers=_auth_header(demo_token)).status_code == 403


def test_ask_works_without_token(client):
    r = client.post("/api/ask", json={"question": "What is total revenue?"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "executed"
    assert body["rows"][0][0] == 517171.84


def test_health_reports_backends(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["dialect"] == "duckdb"
    assert body["cache_backend"] == "memory"
    assert body["scheduler"] == "disabled (no REDIS_URL)"
    assert body["auth"] == "jwt"


# ---------- 4. Insight run-now + threshold logic ----------

def _create_insight(client, token, sql, threshold):
    r = client.post("/api/insights",
                    json={"question": "Revenue watch", "sql": sql,
                          "cadence": "daily", "threshold": threshold},
                    headers=_auth_header(token))
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_insight_run_now_breach_notifies(client, monkeypatch):
    calls = []
    monkeypatch.setattr(insights, "notify_insight",
                        lambda *a, **k: calls.append(a) or {"webhook": {"ok": True}})
    token = _token(client, DEMO_EMAIL, DEMO_PASSWORD)
    ins_id = _create_insight(client, token, REVENUE_SQL, 1000.0)
    try:
        r = client.post(f"/api/insights/{ins_id}/run-now", headers=_auth_header(token))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "executed"
        assert body["value"] == 517171.84
        assert body["breached"] is True
        assert len(calls) == 1  # notified exactly once, on the breach
    finally:
        delete_insight(ins_id)


def test_insight_run_now_no_breach_no_notify(client, monkeypatch):
    calls = []
    monkeypatch.setattr(insights, "notify_insight",
                        lambda *a, **k: calls.append(a) or {"webhook": {"ok": True}})
    token = _token(client, DEMO_EMAIL, DEMO_PASSWORD)
    ins_id = _create_insight(client, token, REVENUE_SQL, 999999.0)
    try:
        r = client.post(f"/api/insights/{ins_id}/run-now", headers=_auth_header(token))
        assert r.status_code == 200, r.text
        assert r.json()["breached"] is False
        assert calls == []  # no breach -> no notification
    finally:
        delete_insight(ins_id)


def test_insight_run_now_paused_on_schema_change(client, monkeypatch):
    calls = []
    monkeypatch.setattr(insights, "notify_insight",
                        lambda *a, **k: calls.append(a) or {"webhook": {"ok": True}})
    token = _token(client, DEMO_EMAIL, DEMO_PASSWORD)
    ins_id = _create_insight(client, token, REVENUE_SQL, 1000.0)
    try:
        # The saved SQL later becomes invalid (column dropped from the layer).
        update_insight(ins_id, {"sql": "SELECT profit FROM orders"})
        r = client.post(f"/api/insights/{ins_id}/run-now", headers=_auth_header(token))
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "paused_on_schema_change"
        assert len(calls) == 1  # owner is alerted about the pause
    finally:
        delete_insight(ins_id)


def test_insight_create_requires_auth(client):
    r = client.post("/api/insights",
                    json={"question": "x", "sql": REVENUE_SQL, "cadence": "daily"})
    assert r.status_code == 401


# ---------- 5. Golden set ----------

def test_golden_set_has_50_cases():
    data = json.loads(GOLDEN_PATH.read_text())
    cases = data["cases"]
    assert len(cases) == 50
    ids = [c["id"] for c in cases]
    assert len(set(ids)) == 50
    for c in cases:
        assert c["expected_status"] in ("answered", "clarifying", "refused", "blocked")
        if c["expected_status"] == "answered":
            assert c.get("check_sql"), f"{c['id']} has no check_sql"
