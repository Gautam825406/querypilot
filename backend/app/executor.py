"""Read-only executor: SQLAlchemy + DuckDB, row cap + timeout enforced in code."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from pathlib import Path

from sqlalchemy import create_engine, text

from .config import settings


def get_engine(db_path: str | None = None):
    path = Path(db_path or settings.database_path).resolve()
    return create_engine(f"duckdb:///{path}", connect_args={"read_only": True})


def execute_sql(sql: str, db_path: str | None = None, timeout: int | None = None):
    """Execute validated SQL only. Returns (columns, rows, duration_ms). Raises on error/timeout."""
    timeout = timeout or settings.timeout_seconds
    engine = get_engine(db_path)

    def _run():
        with engine.connect() as conn:
            result = conn.execute(text(sql))
            cols = list(result.keys())
            rows = [list(r) for r in result.fetchmany(settings.row_limit)]
            return cols, rows

    import time
    start = time.time()
    with ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(_run)
        try:
            cols, rows = fut.result(timeout=timeout)
        except FuturesTimeout:
            raise TimeoutError(f"Query timed out after {timeout}s (statement timeout). Narrow the question and retry.")
    return cols, rows, int((time.time() - start) * 1000)
