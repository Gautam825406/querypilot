"""Plain-English explainer — generated FROM executed result rows, never from the question alone."""
from __future__ import annotations

import os
from decimal import Decimal

from .http import post_json


def _num(v):
    return isinstance(v, (int, float, Decimal))


def _fmt(v):
    if isinstance(v, Decimal):
        v = float(v)
    if isinstance(v, float):
        return f"${v:,.2f}" if abs(v) > 100 else f"{v:,.2f}"
    if isinstance(v, int):
        return f"{v:,}"
    return str(v)


def explain(question: str, metric: str, definition: str, columns, rows, dimension=None, period=None, filters=None) -> str:
    parts = []
    if not rows:
        f = f" with filters {filters}" if filters else ""
        p = f" for {period}" if period else ""
        return (f"No results were found for '{question}'{p}{f}. This is an empty result, not zero — "
                f"the filters and period above returned no matching rows. Try widening the period or removing a filter.")
    if len(rows) == 1 and len(columns) <= 2:
        val = rows[0][-1]
        label = columns[-1].replace("_", " ")
        p = f" for {period}" if period else ""
        f = f" ({filters})" if filters else ""
        parts.append(f"{label.capitalize()} is {_fmt(val)}{p}{f}.")
    elif len(rows) > 1:
        vals = [float(r[-1]) for r in rows if _num(r[-1])]
        if vals:
            top = rows[max(range(len(rows)), key=lambda i: vals[i])]
            bottom = rows[min(range(len(rows)), key=lambda i: vals[i])]
            if "rate" in metric or "average" in metric:
                # Rates/averages must never be summed across groups.
                parts.append(f"Latest value is {_fmt(rows[-1][-1])} ({rows[-1][0]}). "
                             f"Highest is {top[0]} at {_fmt(top[-1])}; lowest is {bottom[0]} at {_fmt(bottom[-1])}.")
            else:
                total = sum(vals)
                parts.append(f"Across {len(rows)} groups, the total is {_fmt(total)}. "
                             f"Highest is {top[0]} at {_fmt(top[-1])}; lowest is {bottom[0]} at {_fmt(bottom[-1])}.")
        else:
            parts.append(f"The query returned {len(rows)} rows.")
    parts.append(f"Metric: {metric} — {definition}")
    if period:
        parts.append(f"Period: {period}.")
    if filters:
        parts.append(f"Filters: {filters}.")
    if dimension:
        parts.append(f"Broken down by {dimension}.")
    return " ".join(parts)


def explain_upload(question: str, dataset_name: str, columns, rows) -> str:
    """Plain-English summary for a question over an uploaded CSV dataset."""
    if not rows:
        return f"No rows matched in '{dataset_name}'."
    if len(rows) == 1 and len(columns) <= 2:
        val = rows[0][-1]
        label = columns[-1].replace("_", " ")
        return f"{label.capitalize()} is {_fmt(val)} in '{dataset_name}'."
    if len(rows) > 1:
        vals = [float(r[-1]) for r in rows if _num(r[-1])]
        if vals and len(columns) >= 2:
            top = rows[max(range(len(rows)), key=lambda i: vals[i])]
            bottom = rows[min(range(len(rows)), key=lambda i: vals[i])]
            return (f"Across {len(rows)} groups in '{dataset_name}', the total is {_fmt(sum(vals))}. "
                    f"Highest is {top[0]} at {_fmt(top[-1])}; lowest is {bottom[0]} at {_fmt(bottom[-1])}.")
        return f"The query returned {len(rows)} rows from '{dataset_name}'."
    return f"The query returned {len(rows)} row from '{dataset_name}'."


def llm_explain(question: str, template: str) -> str:
    """Optional LLM polish for the explanation only. Returns template unchanged on any failure / no key."""
    key = os.environ.get("GROQ_API_KEY", "")
    if not key:
        return template
    try:
        r = post("https://api.groq.com/openai/v1/chat/completions",
                 {"model": "llama-3.3-70b-versatile",
                  "messages": [{"role": "user",
                                "content": f"Rewrite this analytics explanation in plain English, keeping every number exact:\n{template}"}]},
                 headers={"Authorization": f"Bearer {key}"},
                 timeout=15)
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"].strip()
    except Exception:
        pass
    return template
