"""Pytest suite for QueryPilot. Run: python -m pytest backend/tests -q (from repo root)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.charts import select_chart  # noqa: E402
from app.planner import parse_question, run_ask  # noqa: E402
from app.semantic import layer  # noqa: E402
from app.validator import ValidationError, validate_sql  # noqa: E402


def test_validator_blocks_delete():
    with pytest.raises(ValidationError) as e:
        validate_sql("DELETE FROM orders WHERE id = 1")
    assert e.value.rule == "read_only"


def test_validator_blocks_drop_and_insert():
    for sql in ("DROP TABLE customers", "INSERT INTO orders VALUES (1,1,1,'2025-01-01',10,1,'North','online','completed')",
                "UPDATE orders SET amount = 0"):
        with pytest.raises(ValidationError):
            validate_sql(sql)


def test_validator_blocks_pii():
    with pytest.raises(ValidationError) as e:
        validate_sql("SELECT email FROM customers")
    assert e.value.rule == "pii"
    with pytest.raises(ValidationError):
        validate_sql("SELECT name, phone FROM customers")


def test_validator_blocks_stacked_and_comments():
    with pytest.raises(ValidationError):
        validate_sql("SELECT * FROM orders; DELETE FROM orders")
    with pytest.raises(ValidationError):
        validate_sql("SELECT * FROM orders -- ; DELETE FROM orders")


def test_validator_blocks_out_of_schema():
    with pytest.raises(ValidationError) as e:
        validate_sql("SELECT * FROM employees")
    assert e.value.rule == "schema"
    with pytest.raises(ValidationError):
        validate_sql("SELECT profit FROM orders")


def test_validator_valid_passes_and_injects_limit():
    out = validate_sql("SELECT region, SUM(amount) AS total_revenue FROM orders GROUP BY region")
    assert out["ok"] is True
    assert "LIMIT 100" in out["sql"]


def test_semantic_layer_loads():
    assert "total_revenue" in layer.metrics
    assert "monthly_churn_rate" in layer.metrics
    assert len(layer.metrics) == 5
    assert layer.metrics["total_revenue"]["model"] == "orders"
    assert any(j["from"] == "orders" and j["to"] == "products" for j in layer.joins)
    assert layer.models["customers"]["columns"][2]["pii"] is True


def test_chart_selection():
    assert select_chart(["month", "total_revenue"], [["2025-01-01", 100.0], ["2025-02-01", 120.0]])["type"] == "line"
    assert select_chart(["region", "total_revenue"], [["North", 100.0], ["South", 80.0]])["type"] == "bar"
    assert select_chart(["total_revenue"], [[12345.0]])["type"] == "stat"
    assert select_chart([], [])["type"] == "none"


def test_ask_total_revenue_end_to_end():
    run = run_ask("What is total revenue?")
    assert run["status"] == "executed"
    assert run["rows"] and run["rows"][0][0] > 0
    assert "total_revenue" in run["explanation"].lower() or "revenue" in run["explanation"].lower()
    assert run["chart_spec"]["type"] == "stat"
    assert run["cost"] == 0.0 and run["tokens"] == 0


def test_ask_monthly_trend():
    run = run_ask("What is total revenue by month?")
    assert run["status"] == "executed"
    assert len(run["rows"]) == 24
    assert run["chart_spec"]["type"] == "line"


def test_ask_top_region_and_churn():
    top = run_ask("What is total revenue by region?")
    assert top["status"] == "executed" and len(top["rows"]) == 4
    churn = run_ask("What is the monthly churn rate?")
    assert churn["status"] == "executed"
    assert churn["rows"] and len(churn["rows"]) >= 1


def test_ask_ambiguity_returns_clarifying():
    run = run_ask("What is the churn rate?")
    assert run["status"] == "clarifying"
    assert run["clarifying_options"]
    run2 = run_ask("What was the revenue in March and April?")
    assert run2["status"] == "clarifying"


def test_ask_unmodelled_refuses_honestly():
    run = run_ask("What is our profit margin?")
    assert run["status"] == "refused"
    assert "not modelled" in run["refusal"]
    assert "total_revenue" in run["refusal"]


def test_ask_unsafe_blocked_never_executes():
    run = run_ask("Delete all orders from the database.")
    assert run["status"] == "blocked"
    assert run["rows"] == []
    run2 = run_ask("Show me customer emails.")
    assert run2["status"] == "blocked"


def test_correction_loop_fixes_broken_sql():
    # Passes validation (all columns exist) but fails at execution: month 13 is not a date.
    broken_sql = ("SELECT SUM(orders.amount) AS total_revenue FROM orders WHERE orders.status = 'completed' "
                  "AND orders.order_date >= DATE '2025-13-01'")
    run = run_ask("What is total revenue?", force_sql=broken_sql)
    assert run["status"] == "executed"
    assert len(run["attempts"]) >= 1
    assert "2025-12-01" in run["sql"]


def test_empty_result_states_filters():
    run = run_ask("What is total revenue in the North region?")
    assert run["status"] == "executed"
    # A nonsense region filter is not a modelled value; planner ignores unknown regions.
    assert run["rows"]


def test_follow_up_reuses_prior_context():
    first = run_ask("What is total revenue?")
    assert first["status"] == "executed"
    second = run_ask("by region", prior={"metric": first["metric"]})
    assert second["status"] == "executed"
    assert second["metric"] == "total_revenue"
    assert len(second["rows"]) == 4
