"""Evaluation harness: runs the REAL ask path on the golden set and prints
execution accuracy, valid-SQL rate, correction success, the safety suite and
the four-way ablation (PRD §13).

Run:  python backend/evals/run_eval.py   (from repo root)
  or: python -m evals.run_eval           (from backend/)

Ablation configs (same 50 cases each):
  (a) direct_llm_baseline — Groq drafts SQL straight from the schema, no
      semantic layer, no validator. Runs ONLY when an LLM key is set;
      otherwise it reports "not run — no LLM key" and no number is invented.
      A harness guard never executes destructive baseline SQL.
  (b) semantic_layer_only — deterministic draft from the semantic layer,
      executed directly: no validator, no correction, no cache.
  (c) validator_and_correction — full run_ask with the cache bypassed.
  (d) full_system_cached — full run_ask with cache on (second pass measures
      the cache hit rate and warm latency).
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sqlglot  # noqa: E402
from app.config import settings  # noqa: E402
from app.executor import execute_sql  # noqa: E402
from app.planner import build_sql, parse_question, run_ask  # noqa: E402
from app.semantic import SCHEMA  # noqa: E402
from app.validator import ValidationError, validate_sql  # noqa: E402

SAFETY_CASES = [
    ("destructive delete", "DELETE FROM orders WHERE id = 1"),
    ("destructive drop", "DROP TABLE customers"),
    ("destructive update", "UPDATE orders SET amount = 0"),
    ("insert", "INSERT INTO orders VALUES (1, 1, 1, '2025-01-01', 10, 1, 'North', 'online', 'completed')"),
    ("stacked", "SELECT * FROM orders; DELETE FROM orders"),
    ("comment-hidden", "SELECT * FROM orders -- ; DELETE FROM orders"),
    ("pii email", "SELECT email FROM customers"),
    ("pii phone", "SELECT name, phone FROM customers"),
    ("out of schema table", "SELECT * FROM employees"),
    ("out of schema column", "SELECT profit FROM orders"),
    ("cross-workspace table", "SELECT * FROM other_company_orders"),
]


def _norm(rows):
    from decimal import Decimal
    out = []
    for r in rows:
        normed = []
        for v in r:
            if isinstance(v, Decimal):
                normed.append(round(float(v), 2))
            elif isinstance(v, float):
                normed.append(round(v, 2))
            elif hasattr(v, "isoformat"):
                normed.append(v.isoformat())
            else:
                normed.append(v)
        out.append(tuple(normed))
    return sorted(out)


def _compare(answer_cols, answer_rows, ref_cols, ref_rows):
    """Compare on the reference columns present in the answer (subset match).

    Both sides are normalised first (Decimal -> rounded float, dates ->
    ISO strings) — a raw Decimal('54428.14') never equals the float 54428.14
    exactly, so comparing unnormalised rows silently fails every case.
    """
    if not ref_cols:
        return False
    idx = []
    for c in ref_cols:
        if c in answer_cols:
            idx.append(answer_cols.index(c))
        else:
            return False
    a = _norm([tuple(r[i] for i in idx) for r in answer_rows])
    b = _norm(ref_rows)
    return a == b


def _status_matches(got: str, expected: str) -> bool:
    return got == expected


def _run_full_system(cases, use_cache: bool):
    """Config (c)/(d): the real run_ask path. Returns (stats, per_case)."""
    correct = correct_answered = valid_sql = answered = generated = 0
    latencies, cache_hits = [], 0
    per_case = []
    for case in cases:
        started = time.time()
        run = run_ask(case["question"], use_cache=use_cache)
        latencies.append((time.time() - started) * 1000)
        if run.get("cache_hit"):
            cache_hits += 1
        status = run["status"]
        ok_sql = bool(run.get("sql")) and run.get("validation", {}).get("ok", False)
        if run.get("sql"):
            generated += 1
            if ok_sql:
                valid_sql += 1
        passed = False
        if case["expected_status"] == "answered":
            answered += 1
            if status == "executed" and case.get("check_sql"):
                ref_cols, ref_rows, _ = execute_sql(case["check_sql"])
                passed = _compare(run["columns"], run["rows"], ref_cols, ref_rows)
                if passed:
                    correct += 1
                    correct_answered += 1
        else:
            passed = _status_matches(status, case["expected_status"])
            if passed:
                correct += 1
        per_case.append({"id": case["id"], "category": case["category"],
                         "question": case["question"], "expected": case["expected_status"],
                         "got": status, "passed": passed})
    n = len(cases)
    return {
        "execution_accuracy": round(correct / n, 4),
        "execution_accuracy_answered_only": round(correct_answered / max(answered, 1), 4) if answered else None,
        "valid_sql_rate": round(valid_sql / max(generated, 1), 4),
        "correct": correct, "total": n,
        "avg_latency_ms": round(sum(latencies) / max(len(latencies), 1), 1),
        "cache_hit_rate": round(cache_hits / n, 4),
    }, per_case


def _run_layer_only(cases):
    """Config (b): semantic-layer draft, executed directly — no validator,
    no correction, no cache."""
    correct = correct_answered = valid = answered = generated = 0
    latencies = []
    for case in cases:
        started = time.time()
        parsed = parse_question(case["question"])
        status = parsed.get("status")
        if status != "ok":
            latencies.append((time.time() - started) * 1000)
            if _status_matches(status, case["expected_status"]):
                correct += 1
            continue
        generated += 1
        try:
            sql = build_sql(parsed)
            cols, rows, _ = execute_sql(sql)
            valid += 1
            if case["expected_status"] == "answered":
                answered += 1
                if case.get("check_sql"):
                    ref_cols, ref_rows, _ = execute_sql(case["check_sql"])
                    if _compare(cols, rows, ref_cols, ref_rows):
                        correct += 1
                        correct_answered += 1
        except Exception:
            pass  # no correction in this config: a failure stays a failure
        latencies.append((time.time() - started) * 1000)
    n = len(cases)
    return {
        "execution_accuracy": round(correct / n, 4),
        "execution_accuracy_answered_only": round(correct_answered / max(answered, 1), 4) if answered else None,
        "valid_sql_rate": round(valid / max(generated, 1), 4),
        "correct": correct, "total": n,
        "avg_latency_ms": round(sum(latencies) / max(len(latencies), 1), 1),
        "correction_success": None,
        "cache_hit_rate": 0.0,
    }


def _baseline_generate(question: str) -> str | None:
    """Direct-LLM SQL from the schema only (no semantic layer). Groq only."""
    from app.http import post
    schema_text = "\n".join(
        f"- {table}({', '.join(cols)})" for table, cols in SCHEMA.items())
    prompt = (
        "You are a text-to-SQL assistant. Database: DuckDB. Tables:\n" + schema_text +
        "\nWrite a single DuckDB SELECT query that answers this question. "
        "Reply with ONLY the SQL, no explanation, no markdown.\nQuestion: " + question)
    try:
        r = post("https://api.groq.com/openai/v1/chat/completions",
                 {"model": "llama-3.3-70b-versatile",
                  "messages": [{"role": "user", "content": prompt}],
                  "temperature": 0},
                 headers={"Authorization": f"Bearer {settings.groq_api_key}"},
                 timeout=30)
        if r.status_code != 200:
            return None
        text = r.json()["choices"][0]["message"]["content"].strip()
        text = re.sub(r"^```(?:sql)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        return text.strip()
    except Exception:
        return None


def _run_baseline(cases):
    """Config (a): direct LLM SQL. Runs ONLY with an LLM key set.

    Safety: the harness guard parses the baseline SQL and NEVER executes
    anything that is not a single SELECT. A destructive draft is recorded
    as an unsafe attempt, not executed.
    """
    if not settings.groq_api_key:
        return {"status": "not run — no LLM key",
                "note": "Direct-LLM baseline needs GROQ_API_KEY. No baseline "
                        "number is claimed without running it."}
    correct = correct_answered = valid = answered = generated = 0
    unsafe_attempts = []
    latencies = []
    for case in cases:
        started = time.time()
        sql = _baseline_generate(case["question"])
        latencies.append((time.time() - started) * 1000)
        if not sql:
            continue
        generated += 1
        try:
            tree = sqlglot.parse_one(sql, read="duckdb")
        except Exception:
            continue
        from sqlglot import exp as _exp
        if not isinstance(tree, _exp.Select):
            if re.search(r"\b(delete|drop|update|insert|truncate|alter)\b", sql, re.I):
                unsafe_attempts.append(case["id"])
            continue
        try:
            cols, rows, _ = execute_sql(tree.sql(dialect="duckdb"))
            valid += 1
        except Exception:
            continue
        if case["expected_status"] == "answered":
            answered += 1
            if case.get("check_sql"):
                ref_cols, ref_rows, _ = execute_sql(case["check_sql"])
                if _compare(cols, rows, ref_cols, ref_rows):
                    correct += 1
                    correct_answered += 1
        # Non-answered cases: the baseline always answers, so it guessed —
        # incorrect by construction (it cannot clarify, refuse or block).
    n = len(cases)
    return {
        "status": "run",
        "execution_accuracy": round(correct / n, 4),
        "execution_accuracy_answered_only": round(correct_answered / max(answered, 1), 4) if answered else None,
        "valid_sql_rate": round(valid / max(generated, 1), 4),
        "correct": correct, "total": n,
        "avg_latency_ms": round(sum(latencies) / max(len(latencies), 1), 1),
        "unsafe_attempts_blocked_by_harness": unsafe_attempts,
        "correction_success": None,
        "cache_hit_rate": 0.0,
    }


def main():
    golden = json.loads((Path(__file__).parent / "golden.json").read_text())
    cases = golden["cases"]

    # Config (d): full system, cache on. First pass populates the cache;
    # a second pass measures the warm cache hit rate.
    stats_d, per_case = _run_full_system(cases, use_cache=True)
    warm, _ = _run_full_system(cases, use_cache=True)
    stats_d["cache_hit_rate"] = warm["cache_hit_rate"]
    stats_d["avg_latency_ms_warm"] = warm["avg_latency_ms"]

    # Config (c): full system, cache bypassed.
    stats_c, _ = _run_full_system(cases, use_cache=False)

    # Config (b): semantic layer only.
    stats_b = _run_layer_only(cases)

    # Config (a): direct-LLM baseline (only with a key).
    stats_a = _run_baseline(cases)

    # Correction loop: a draft that passes validation but fails at execution
    # (invalid DATE literal, month 13) must be fixed from the DB error.
    broken_sql = ("SELECT SUM(orders.amount) AS total_revenue FROM orders "
                  "WHERE orders.status = 'completed' AND orders.order_date >= DATE '2025-13-01'")
    broken = run_ask("What is total revenue?", force_sql=broken_sql)
    correction_ok = broken["status"] == "executed" and len(broken.get("attempts", [])) >= 1

    # Safety suite: every seeded unsafe SQL must be blocked before execution.
    safety = []
    for name, sql in SAFETY_CASES:
        try:
            validate_sql(sql)
            safety.append({"case": name, "blocked": False})
        except ValidationError:
            safety.append({"case": name, "blocked": True})
    safety_blocked = sum(1 for s in safety if s["blocked"])

    result = {
        "golden_cases": len(cases),
        "execution_accuracy": stats_d["execution_accuracy"],
        "execution_accuracy_answered_only": stats_d["execution_accuracy_answered_only"],
        "valid_sql_rate": stats_d["valid_sql_rate"],
        "correction_success": correction_ok,
        "correction_attempts_on_broken_draft": len(broken.get("attempts", [])),
        "safety_suite": {"blocked": safety_blocked, "total": len(safety),
                         "pass_rate": round(safety_blocked / len(safety), 4), "cases": safety},
        "per_case": per_case,
        "ablation": {
            "direct_llm_baseline": stats_a,
            "semantic_layer_only": stats_b,
            "validator_and_correction": stats_c,
            "full_system_cached": stats_d,
        },
    }
    print(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    main()
