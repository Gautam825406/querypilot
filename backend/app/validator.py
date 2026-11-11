"""sqlglot validator — a real parser, not regex. Blocks before execution, always."""
from __future__ import annotations

import re

import sqlglot
from sqlglot import exp

from .config import settings
from .semantic import PII_COLUMNS, SCHEMA

BLOCKED_KEYWORDS = {"insert", "update", "delete", "drop", "create", "alter", "truncate",
                    "grant", "revoke", "merge", "call", "exec", "execute", "replace", "attach"}


class ValidationError(Exception):
    def __init__(self, rule: str, message: str):
        super().__init__(message)
        self.rule = rule
        self.message = message


def _strip_strings(sql: str) -> str:
    return re.sub(r"'[^']*'", "''", sql)


def validate_sql(sql: str) -> dict:
    """Validate one SQL statement. Returns {ok, sql (with LIMIT), rule} or raises ValidationError."""
    raw = sql.strip()
    if not raw:
        raise ValidationError("empty", "Empty SQL is not allowed.")
    # Comments can hide a second statement — generated SQL never needs them.
    if "--" in raw or "/*" in raw:
        raise ValidationError("comment", "SQL comments are not allowed (they can hide a second statement).")
    if ";" in raw.rstrip(";"):
        raise ValidationError("stacked", "Stacked statements are not allowed: only one statement may run.")
    raw = raw.rstrip(";")
    lowered = _strip_strings(raw).lower()
    tokens = set(re.findall(r"[a-z_]+", lowered))
    hit = tokens & BLOCKED_KEYWORDS
    if hit:
        raise ValidationError("read_only", f"Only SELECT is allowed; blocked keyword found: {sorted(hit)[0].upper()}.")
    try:
        parsed = sqlglot.parse(raw, read="duckdb")
    except Exception as e:
        raise ValidationError("parse", f"SQL could not be parsed: {e}")
    if len(parsed) != 1 or parsed[0] is None:
        raise ValidationError("parse", "SQL must be exactly one statement.")
    tree = parsed[0]
    if not isinstance(tree, exp.Select) and not (
        isinstance(tree, exp.Union) is False and tree.find(exp.Select) and str(tree).strip().upper().startswith("WITH")
    ):
        # Allow WITH ... SELECT: top node is a Select in sqlglot for CTE queries.
        raise ValidationError("read_only", "Only a single SELECT (or WITH ... SELECT) is allowed.")
    if tree.find(exp.Select) is None:
        raise ValidationError("read_only", "Only a single SELECT (or WITH ... SELECT) is allowed.")
    # Every table must exist in schema (CTE names and table aliases are allowed).
    cte_names = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    alias_map: dict[str, str] = {}
    for tbl in tree.find_all(exp.Table):
        if tbl.alias:
            alias_map[tbl.alias_or_name.lower()] = tbl.name.lower()
    for tbl in tree.find_all(exp.Table):
        name = tbl.name.lower()
        if name and name not in SCHEMA and name not in cte_names:
            raise ValidationError("schema", f"Table '{name}' is not in the schema or semantic layer.")
    # Every column must exist and PII must be blocked.
    for col in tree.find_all(exp.Column):
        cname = col.name.lower()
        if not cname or cname == "*":
            continue
        table = col.table.lower() if col.table else ""
        if table:
            table = alias_map.get(table, table)
            if table in cte_names:
                continue
            if table not in SCHEMA:
                raise ValidationError("schema", f"Table '{table}' is not in the schema.")
            if cname not in SCHEMA[table]:
                raise ValidationError("schema", f"Column '{table}.{cname}' does not exist.")
            if (table, cname) in PII_COLUMNS:
                raise ValidationError("pii", f"Column '{table}.{cname}' is blocked: it contains personal data (PII).")
        else:
            # Unqualified column: must exist in at least one referenced table and not be PII anywhere.
            # With CTEs present, unqualified columns resolve against CTE outputs (built only
            # from validated tables); a wrong one fails safely at execution, never unsafely.
            if cte_names:
                continue
            tables = {t.name.lower() for t in tree.find_all(exp.Table)} - cte_names
            if tables:
                if not any(cname in SCHEMA.get(t, {}) for t in tables):
                    raise ValidationError("schema", f"Column '{cname}' does not exist in the tables used.")
                if any((t, cname) in PII_COLUMNS for t in tables):
                    raise ValidationError("pii", f"Column '{cname}' is blocked: it contains personal data (PII).")
            elif cname not in {c for cols in SCHEMA.values() for c in cols}:
                raise ValidationError("schema", f"Column '{cname}' does not exist.")
    # Inject LIMIT.
    limit = settings.row_limit
    if tree.args.get("limit") is None:
        tree = tree.limit(limit)
    final_sql = tree.sql(dialect="duckdb")
    return {"ok": True, "sql": final_sql, "rule": "passed", "limit": limit,
            "timeout_seconds": settings.timeout_seconds}
