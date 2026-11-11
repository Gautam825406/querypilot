"""Chart selection from result shape -> Vega-Lite spec (stored with every run)."""
from __future__ import annotations


def _is_month_like(values) -> bool:
    import re
    return bool(values) and all(re.match(r"^\d{4}-\d{2}", str(v)) for v in values)


def select_chart(columns: list[str], rows: list[list]) -> dict:
    if not rows or not columns:
        return {"type": "none", "mark": "text", "encoding": {}, "data": {"values": []}}
    data = [dict(zip(columns, r)) for r in rows]
    if len(rows) == 1 and len(columns) <= 2:
        return {"type": "stat", "mark": "text",
                "encoding": {"value": {"field": columns[-1]}},
                "data": {"values": data}}
    first = [r[0] for r in rows]
    numeric_last = all(isinstance(r[-1], (int, float)) for r in rows)
    if len(columns) >= 2 and _is_month_like(first) and numeric_last:
        return {"type": "line", "mark": "line",
                "encoding": {"x": {"field": columns[0], "type": "temporal"},
                             "y": {"field": columns[-1], "type": "quantitative"}},
                "data": {"values": data}}
    if len(columns) >= 2 and numeric_last and len(rows) > 1:
        return {"type": "bar", "mark": "bar",
                "encoding": {"x": {"field": columns[0], "type": "nominal"},
                             "y": {"field": columns[-1], "type": "quantitative"}},
                "data": {"values": data}}
    return {"type": "table", "mark": "text", "encoding": {}, "data": {"values": data}}
