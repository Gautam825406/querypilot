"""Evaluation harness: runs the REAL ask path on the golden set and prints
execution accuracy, valid-SQL rate, correction success and the safety suite.
Run:  python backend/evals/run_eval.py   (from repo root)
  or: python -m evals.run_eval           (from backend/)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.executor import execute_sql  # noqa: E402
from app.planner import run_ask  # noqa: E402
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
    """Compare on the reference columns present in the answer (subset match)."""
    if not ref_cols:
        return False
    idx = []
    for c in ref_cols:
        if c in answer_cols:
            idx.append(answer_cols.index(c))
        else:
            return False
    a = sorted(tuple(round(r[i], 2) if isinstance(r[i], float) else
                     str(r[i]) if hasattr(r[i], "isoformat") else r[i] for i in idx)
               for r in answer_rows)
    b = _norm(ref_rows)
    return a == b


def main():
    golden = json.loads((Path(__file__).parent / "golden.json").read_text())
    cases = golden["cases"]
    correct = correct_answered = valid_sql = answered = generated = 0
    per_case = []
    for case in cases:
        run = run_ask(case["question"])
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
            passed = status in ("clarifying", "refused", "blocked") and \
                (case["expected_status"] == status or
                 (case["expected_status"] == "clarifying" and status == "clarifying") or
                 (case["expected_status"] == "refused" and status == "refused") or
                 (case["expected_status"] == "blocked" and status == "blocked"))
            if passed:
                correct += 1
        per_case.append({"id": case["id"], "category": case["category"],
                         "question": case["question"], "expected": case["expected_status"],
                         "got": status, "passed": passed})
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
        "execution_accuracy": round(correct / len(cases), 4),
        "execution_accuracy_answered_only": round(correct_answered / max(answered, 1), 4) if answered else None,
        "valid_sql_rate": round(valid_sql / max(generated, 1), 4),
        "correction_success": correction_ok,
        "correction_attempts_on_broken_draft": len(broken.get("attempts", [])),
        "safety_suite": {"blocked": safety_blocked, "total": len(safety),
                         "pass_rate": round(safety_blocked / len(safety), 4), "cases": safety},
        "per_case": per_case,
    }
    print(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    main()
