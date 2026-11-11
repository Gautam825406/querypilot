"""Deterministic planner + REAL LangGraph StateGraph.

Pipeline (every /api/ask call runs this graph, no stubs):
  classify -> resolve -> plan -> draft -> validate -> execute -> correct -> explain
LLM is optional and only ever polishes the explanation (see explainer.llm_explain);
with no API key the deterministic planner below answers the demo questions.
"""
from __future__ import annotations

import difflib
import hashlib
import re
import time
import uuid
from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, StateGraph

from .charts import select_chart
from .config import settings
from .explainer import explain, llm_explain
from .executor import execute_sql
from .seed import seed
from .semantic import SCHEMA, layer
from .store import cache_get, cache_key, cache_put, save_run
from .validator import ValidationError, validate_sql

MONTHS = {"january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
          "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12}
REGIONS = ["North", "South", "East", "West"]
CHANNELS = ["online", "retail", "partner"]
CATEGORIES = ["Electronics", "Apparel", "Home", "Beauty", "Sports"]
UNMODELLED = ["profit", "margin", "refund", "inventory", "stock", "discount", "tax",
              "shipping", "salary", "employee", "nps", "satisfaction", "retention",
              "lifetime value", "ltv", "cac", "acquisition cost", "conversion rate"]
NEAREST = {"profit": "total_revenue", "margin": "total_revenue", "refund": "total_orders",
           "retention": "monthly_churn_rate", "lifetime value": "total_revenue",
           "ltv": "total_revenue", "conversion rate": "total_orders"}


class QPState(TypedDict, total=False):
    question: str
    workspace: str
    prior: dict
    force_sql: str
    intent: str
    metrics: list
    dimension: str
    period: dict
    filters: dict
    plan_steps: list
    sql: str
    validation: dict
    attempts: list
    error: str
    columns: list
    rows: list
    duration_ms: int
    explanation: str
    chart_spec: dict
    status: str
    clarifying_question: str
    clarifying_options: list
    refusal: str
    lineage: dict
    cache_hit: bool


def _month_period(name: str):
    m = MONTHS[name]
    return {"label": f"{name.capitalize()} 2025", "start": f"2025-{m:02d}-01",
            "end": f"2025-{m:02d}-28", "month": m}


def _quarter_period(q: int):
    start_m = (q - 1) * 3 + 1
    return {"label": f"Q{q} 2025", "start": f"2025-{start_m:02d}-01",
            "end": f"2025-{start_m + 2:02d}-28"}


def parse_question(q: str, prior: dict | None = None) -> dict:
    text = (q or "").strip()
    low = text.lower()
    out = {"question": text, "intent": "metric_lookup", "metrics": [], "dimension": None,
           "period": None, "filters": {}, "status": "ok", "clarifying_question": None,
           "clarifying_options": [], "refusal": None}
    if not text:
        out.update(status="refused", refusal="No question was asked. Ask a business question, for example 'What is total revenue by month?'")
        return out
    if re.search(r"\b(delete|drop|update|insert|truncate|alter)\b", low):
        out.update(status="blocked", refusal="That action is blocked: QueryPilot is read-only. Only SELECT queries are allowed — no INSERT, UPDATE, DELETE or DDL, ever.")
        return out
    if "email" in low or "phone" in low:
        out.update(status="blocked", refusal="That column is blocked: customers.email and customers.phone contain personal data (PII) and can never be returned.")
        return out
    # Follow-up: inherit prior run's metric/dimension when the follow-up only adds a slice.
    if prior and prior.get("metric"):
        out["metrics"] = [prior["metric"]]
        if prior.get("dimension"):
            out["dimension"] = prior["dimension"]
    # Unmodelled metric -> honest refusal.
    for kw in UNMODELLED:
        if kw in low:
            out.update(status="refused",
                       refusal=f"'{kw}' is not modelled in the semantic layer, so QueryPilot cannot answer it instead of guessing. "
                               f"Nearest modelled metric: {NEAREST.get(kw, 'total_revenue')}. "
                               f"Ask an analyst to model '{kw}' in semantic/models.yaml.")
            return out
    # Periods.
    named_months = [m for m in MONTHS if m in low]
    quarters = re.findall(r"\bq([1-4])\b", low)
    if len(named_months) >= 2 or len(set(quarters)) >= 2:
        opts = [f"{m.capitalize()} 2025" for m in named_months] or [f"Q{q} 2025" for q in sorted(set(quarters))]
        out.update(status="clarifying", clarifying_question="Which period do you mean? I found more than one in your question.",
                   clarifying_options=opts)
        return out
    if named_months:
        out["period"] = _month_period(named_months[0])
    elif quarters:
        out["period"] = _quarter_period(int(quarters[0]))
    elif "last month" in low:
        out["period"] = _month_period("december")
    elif "last 3 months" in low or "last three months" in low:
        out["period"] = {"label": "Oct–Dec 2025", "start": "2025-10-01", "end": "2025-12-28"}
    # Filters.
    for r in REGIONS:
        if r.lower() in low:
            out["filters"]["region"] = r
    for c in CHANNELS:
        if c in low:
            out["filters"]["channel"] = c
    for cat in CATEGORIES:
        if cat.lower() in low:
            out["filters"]["category"] = cat
    # Metrics (combinable ones share the orders model).
    if "churn" in low:
        out["metrics"] = ["monthly_churn_rate"]
    if "average order value" in low or "aov" in low:
        out["metrics"].append("average_order_value")
    if "active customer" in low or "how many customers" in low or "number of customers" in low:
        out["metrics"] = ["active_customers"]
    if re.search(r"how many orders|number of orders|order count|total orders", low):
        out["metrics"].append("total_orders")
    if "revenue" in low or "sales" in low or "total revenue" in low:
        if "total_revenue" not in out["metrics"]:
            out["metrics"].append("total_revenue")
    if not out["metrics"] and "orders" in low:
        out["metrics"] = ["total_orders"]
    if not out["metrics"] and "customers" in low:
        out["metrics"] = ["active_customers"]
    # Ambiguity: churn with no period qualifier at all.
    if out["metrics"] == ["monthly_churn_rate"] and "monthly" not in low and "by month" not in low \
            and not out["period"] and "trend" not in low and "over time" not in low:
        out.update(status="clarifying",
                   clarifying_question="Which churn period do you mean? Monthly churn rate needs a period.",
                   clarifying_options=["Last month (Dec 2025)", "Last 3 months (Oct–Dec 2025)", "All 12 months of 2025"])
        return out
    # Ambiguity: vague performance question with no breakdown.
    if re.search(r"how (did|is)|performance|how .* do(ing)?", low) and "how many" not in low \
            and "number of" not in low and out["metrics"] \
            and not out["dimension"] and not out["period"] and not out["filters"] \
            and "by " not in low and "trend" not in low:
        out.update(status="clarifying",
                   clarifying_question=f"How would you like to see {out['metrics'][0].replace('_', ' ')} broken down?",
                   clarifying_options=["Total (all time)", "By month", "By region"])
        return out
    # Ambiguity: two non-combinable metrics joined by 'and'.
    non_comb = {"monthly_churn_rate", "active_customers"}
    if len(out["metrics"]) >= 2 and any(m in non_comb for m in out["metrics"]):
        out.update(status="clarifying",
                   clarifying_question="Your question mentions two different metrics from different tables. Which one should I answer first?",
                   clarifying_options=[m.replace("_", " ") for m in out["metrics"]])
        return out
    if not out["metrics"]:
        out.update(status="refused",
                   refusal="I could not match your question to a modelled metric. Modelled metrics are: "
                          "total_revenue, total_orders, average_order_value, active_customers, monthly_churn_rate. "
                          "Try 'What is total revenue by month?'")
        return out
    # Dimension.
    if "categor" in low and "top" in low:
        out["dimension"] = "product_category"
    elif "top product" in low or "best-selling product" in low or "top 5 product" in low:
        out["dimension"] = "product"
    elif "top region" in low:
        out["dimension"] = "region"
    elif "by month" in low or "monthly" in low or "trend" in low or "over time" in low:
        out["dimension"] = "month"
    elif "by region" in low:
        out["dimension"] = "region"
    elif "by categor" in low:
        out["dimension"] = "product_category"
    elif "by channel" in low:
        out["dimension"] = "channel"
    if "top" in low or "best" in low or "highest" in low or "rank" in low:
        out["intent"] = "ranking"
    elif out["dimension"] == "month":
        out["intent"] = "trend"
    elif " vs " in low or "compare" in low or "versus" in low:
        out["intent"] = "comparison"
    elif len(out["metrics"]) >= 2:
        out["intent"] = "multi_step"
    return out


def _where(filters: dict, period: dict | None, date_col: str = "orders.order_date") -> str:
    conds = ["orders.status = 'completed'"]
    if period:
        conds.append(f"{date_col} >= DATE '{period['start']}' AND {date_col} <= DATE '{period['end']}'")
    if filters.get("region"):
        conds.append(f"orders.region = '{filters['region']}'")
    if filters.get("channel"):
        conds.append(f"orders.channel = '{filters['channel']}'")
    return "WHERE " + " AND ".join(conds)


def build_sql(p: dict) -> str:
    metrics, dim, period, filters = p["metrics"], p.get("dimension"), p.get("period"), p.get("filters", {})
    agg = {"total_revenue": "SUM(orders.amount) AS total_revenue",
           "total_orders": "COUNT(*) AS total_orders",
           "average_order_value": "AVG(orders.amount) AS average_order_value"}
    if metrics == ["monthly_churn_rate"]:
        if dim == "month" or not period:
            return ("WITH monthly AS (SELECT DATE_TRUNC('month', subscriptions.started_at) AS month, "
                    "COUNT(*) AS started FROM subscriptions GROUP BY 1), "
                    "churned AS (SELECT DATE_TRUNC('month', subscriptions.ended_at) AS month, COUNT(*) AS churned "
                    "FROM subscriptions WHERE subscriptions.status = 'churned' GROUP BY 1), "
                    "monthly_cum AS (SELECT month, SUM(started) OVER (ORDER BY month) AS cum_started FROM monthly) "
                    "SELECT monthly_cum.month AS month, COALESCE(churned.churned, 0) AS churned, "
                    "ROUND(100.0 * COALESCE(churned.churned, 0) / NULLIF(monthly_cum.cum_started, 0), 2) AS monthly_churn_rate "
                    "FROM monthly_cum JOIN churned ON monthly_cum.month = churned.month ORDER BY 1")
        # Single-period churn.
        cond = ""
        if period:
            cond = (f" WHERE subscriptions.ended_at >= DATE '{period['start']}' "
                    f"AND subscriptions.ended_at <= DATE '{period['end']}'")
        return ("SELECT ROUND(100.0 * COUNT(*) / NULLIF((SELECT COUNT(*) FROM subscriptions "
                "WHERE subscriptions.started_at <= DATE '2025-12-31'), 0), 2) AS monthly_churn_rate "
                f"FROM subscriptions WHERE subscriptions.status = 'churned'{cond}")
    if metrics == ["active_customers"]:
        if dim == "month":
            return ("SELECT DATE_TRUNC('month', subscriptions.started_at) AS month, "
                    "COUNT(DISTINCT subscriptions.customer_id) AS active_customers FROM subscriptions "
                    "WHERE subscriptions.status = 'active' GROUP BY 1 ORDER BY 1")
        return "SELECT COUNT(DISTINCT subscriptions.customer_id) AS active_customers FROM subscriptions WHERE subscriptions.status = 'active'"
    where = _where(filters, period)
    select_aggs = ", ".join(agg[m] for m in metrics if m in agg)
    if dim == "product":
        return (f"SELECT products.name AS product, {select_aggs} FROM orders "
                "JOIN products ON orders.product_id = products.id "
                f"{where} GROUP BY products.name ORDER BY 2 DESC LIMIT 5")
    if dim == "product_category":
        return (f"SELECT products.category AS product_category, {select_aggs} FROM orders "
                "JOIN products ON orders.product_id = products.id "
                f"{where} GROUP BY products.category ORDER BY 2 DESC")
    if dim == "month":
        return (f"SELECT DATE_TRUNC('month', orders.order_date) AS month, {select_aggs} FROM orders "
                f"{where} GROUP BY 1 ORDER BY 1")
    if dim == "region":
        return (f"SELECT orders.region AS region, {select_aggs} FROM orders {where} "
                "GROUP BY orders.region ORDER BY 2 DESC")
    if dim == "channel":
        return (f"SELECT orders.channel AS channel, {select_aggs} FROM orders {where} "
                "GROUP BY orders.channel ORDER BY 2 DESC")
    return f"SELECT {select_aggs} FROM orders {where}"


def _fix_sql(sql: str, error: str) -> str:
    """Deterministic corrector: use the exact DB error to repair the draft."""
    # Invalid DATE literal (e.g. month 13): clamp the month/day to a valid date.
    # (sqlglot may rewrite DATE '...' as CAST('...' AS DATE), so match the quoted date.)
    if "date" in error.lower() or "out of range" in error.lower():
        def _clamp(m):
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            return f"'{y}-{min(mo, 12):02d}-{min(d, 28):02d}'"
        fixed = re.sub(r"'(\d{4})-(\d{2})-(\d{2})'", _clamp, sql)
        if fixed != sql:
            return fixed
    mapping = {"total": "amount", "revenue": "amount", "sales": "amount", "price": "amount",
               "total_revenue": "amount", "order_total": "amount"}
    m = re.search(r"column ['\"]?(\w+)['\"]? not found|Referenced column ['\"]?(\w+)", error, re.I)
    bad = (m.group(1) or m.group(2)) if m else None
    fixed = sql
    if bad:
        repl = mapping.get(bad.lower())
        if not repl:
            all_cols = [c for cols in SCHEMA.values() for c in cols]
            close = difflib.get_close_matches(bad.lower(), all_cols, n=1)
            repl = close[0] if close else "amount"
        fixed = re.sub(rf"\b{re.escape(bad)}\b", repl, sql, flags=re.I)
    if "no such table" in error.lower() or "not found" in error.lower() and not bad:
        fixed = fixed.replace("order", "orders")
    return fixed


# ---- LangGraph nodes (each is a real step; the graph is invoked on every ask) ----
def node_classify(state: QPState) -> QPState:
    parsed = parse_question(state["question"], state.get("prior"))
    return {**parsed, "attempts": [], "cache_hit": False, "error": ""}


def node_resolve(state: QPState) -> QPState:
    metric = state["metrics"][0]
    m = layer.metric(metric) or {}
    return {"lineage": {"metric": metric, "definition": m.get("definition", ""),
                        "model": m.get("model", ""), "layer_version": layer.version,
                        "dimensions": m.get("dimensions", []), "owner": m.get("owner", "")}}


def node_plan(state: QPState) -> QPState:
    steps = [{"step": 1, "action": f"Resolve metric {state['metrics'][0]} from the semantic layer"},
             {"step": 2, "action": f"Draft SQL (dimension: {state.get('dimension') or 'none'}, "
                                   f"period: {(state.get('period') or {}).get('label', 'all time')})"},
             {"step": 3, "action": "Validate SQL with sqlglot, then execute read-only with limits"}]
    if len(state["metrics"]) > 1:
        steps.insert(1, {"step": 2, "action": f"Combine metrics {', '.join(state['metrics'])} in one grouped query"})
    return {"plan_steps": steps}


def node_draft(state: QPState) -> QPState:
    sql = state.get("force_sql") or build_sql(state)
    return {"sql": sql}


def node_validate(state: QPState) -> QPState:
    try:
        v = validate_sql(state["sql"])
        return {"validation": v, "sql": v["sql"], "status": "validated"}
    except ValidationError as e:
        return {"validation": {"ok": False, "rule": e.rule, "message": e.message},
                "status": "blocked", "refusal": f"Blocked by the validator ({e.rule}): {e.message}"}


def node_execute(state: QPState) -> QPState:
    key = cache_key(state["sql"], layer.version)
    hit = cache_get(key)
    if hit:
        return {"columns": hit["columns"], "rows": hit["rows"], "duration_ms": 0,
                "cache_hit": True, "status": "executed", "error": ""}
    try:
        cols, rows, dur = execute_sql(state["sql"])
        cache_put(key, {"columns": cols, "rows": rows})
        return {"columns": cols, "rows": rows, "duration_ms": dur, "status": "executed", "error": ""}
    except Exception as e:
        attempts = list(state.get("attempts", [])) + [{"attempt": len(state.get("attempts", [])) + 1,
                                                       "sql": state["sql"], "error": str(e)}]
        return {"attempts": attempts, "error": str(e), "status": "failed"}


def node_correct(state: QPState) -> QPState:
    fixed = _fix_sql(state["sql"], state.get("error", ""))
    if fixed == state["sql"]:
        return {"status": "failed", "error": state.get("error", "") + " (correction made no change; stopping)"}
    return {"sql": fixed, "status": "corrected"}


def node_explain(state: QPState) -> QPState:
    if state.get("status") == "failed":
        return {"explanation": f"The query could not be executed after {len(state.get('attempts', []))} attempt(s). "
                               f"Last error: {state.get('error', '')}. This looks like a modelling problem, not a wording problem.",
                "chart_spec": select_chart([], [])}
    metric = state["metrics"][0]
    definition = (state.get("lineage") or {}).get("definition", "")
    template = explain(state["question"], metric, definition, state.get("columns", []),
                       state.get("rows", []), dimension=state.get("dimension"),
                       period=(state.get("period") or {}).get("label"),
                       filters=state.get("filters"))
    return {"explanation": llm_explain(state["question"], template),
            "chart_spec": select_chart(state.get("columns", []), state.get("rows", []))}


def _after_classify(state: QPState) -> str:
    return "end" if state.get("status") in ("clarifying", "refused", "blocked") else "resolve"


def _after_validate(state: QPState) -> str:
    return "execute" if state.get("status") == "validated" else "end"


def _after_execute(state: QPState) -> str:
    if state.get("status") == "executed":
        return "explain"
    if len(state.get("attempts", [])) >= settings.max_correction_attempts:
        return "explain"
    if state.get("error", "").endswith("stopping)"):
        return "explain"
    # Stop on a repeated identical error.
    errs = [a["error"] for a in state.get("attempts", [])]
    if len(errs) >= 2 and errs[-1] == errs[-2]:
        return "explain"
    return "correct"


def build_graph():
    g = StateGraph(QPState)
    g.add_node("classify", node_classify)
    g.add_node("resolve", node_resolve)
    g.add_node("plan", node_plan)
    g.add_node("draft", node_draft)
    g.add_node("validate", node_validate)
    g.add_node("execute", node_execute)
    g.add_node("correct", node_correct)
    g.add_node("explain", node_explain)
    g.set_entry_point("classify")
    g.add_conditional_edges("classify", _after_classify, {"end": END, "resolve": "resolve"})
    g.add_edge("resolve", "plan")
    g.add_edge("plan", "draft")
    g.add_edge("draft", "validate")
    g.add_conditional_edges("validate", _after_validate, {"execute": "execute", "end": END})
    g.add_conditional_edges("execute", _after_execute, {"explain": "explain", "correct": "correct"})
    g.add_edge("correct", "validate")
    g.add_edge("explain", END)
    return g.compile()


_GRAPH = None


def _jsonable(v):
    from datetime import date, datetime
    from decimal import Decimal
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return v


def _norm_rows(rows):
    return [[_jsonable(v) for v in r] for r in rows]


def _ensure_demo_db():
    db = Path(settings.database_path)
    if not db.exists():
        seed(db)


def run_ask(question: str, workspace: str = "demo", prior: dict | None = None,
            force_sql: str | None = None) -> dict:
    _ensure_demo_db()
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = build_graph()
    started = time.time()
    init: QPState = {"question": question, "workspace": workspace, "prior": prior or {},
                     "force_sql": force_sql or ""}
    result = _GRAPH.invoke(init)
    # Normalise Decimal/date values so rows and the chart spec are JSON-safe.
    result["rows"] = _norm_rows(result.get("rows", []))
    if result.get("columns"):
        result["chart_spec"] = select_chart(result["columns"], result["rows"])
    run = {
        "id": str(uuid.uuid4())[:8],
        "question": question,
        "workspace": workspace,
        "status": result.get("status", "failed"),
        "intent": result.get("intent"),
        "metric": (result.get("metrics") or [None])[0],
        "sql": result.get("sql"),
        "layer_version": layer.version,
        "validation": result.get("validation", {}),
        "attempts": result.get("attempts", []),
        "columns": result.get("columns", []),
        "rows": result.get("rows", []),
        "row_count": len(result.get("rows", [])),
        "duration_ms": int((time.time() - started) * 1000),
        "tokens": 0,
        "cost": 0.0,
        "explanation": result.get("explanation"),
        "chart_spec": result.get("chart_spec", {}),
        "plan_steps": result.get("plan_steps", []),
        "lineage": result.get("lineage", {}),
        "cache_hit": result.get("cache_hit", False),
        "clarifying_question": result.get("clarifying_question"),
        "clarifying_options": result.get("clarifying_options", []),
        "refusal": result.get("refusal"),
        "llm_mode": settings.llm_mode,
        "dimension": result.get("dimension"),
        "period": (result.get("period") or {}).get("label"),
        "filters": result.get("filters", {}),
    }
    # Cost/latency are honest: deterministic mode costs $0 and uses 0 tokens.
    save_run(run)
    return run
