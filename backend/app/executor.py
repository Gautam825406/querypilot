"""Read-only executor: SQLAlchemy dialect abstraction (FR-15).

- DuckDB (default): file-based demo database, read_only connection.
- PostgreSQL (DATABASE_URL set): the same validated SQL runs against Postgres
  inside a READ ONLY transaction with a statement timeout, so the executor
  can never write even if validation had a bug. Row cap + timeout enforced.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from pathlib import Path

from sqlalchemy import create_engine, text

from .config import settings

_PG_ENGINE = None


def get_dialect() -> str:
    return settings.dialect


def get_engine(db_path: str | None = None):
    """SQLAlchemy engine for the configured dialect. Postgres engines are
    cached (pool); DuckDB engines are cheap to build per call."""
    global _PG_ENGINE
    if settings.dialect == "postgres":
        if _PG_ENGINE is None:
            _PG_ENGINE = create_engine(
                settings.database_url, pool_pre_ping=True, pool_size=5, max_overflow=5)
        return _PG_ENGINE
    path = Path(db_path or settings.database_path).resolve()
    return create_engine(f"duckdb:///{path}", connect_args={"read_only": True})


def execute_sql(sql: str, db_path: str | None = None, timeout: int | None = None):
    """Execute validated SQL only. Returns (columns, rows, duration_ms). Raises on error/timeout."""
    timeout = timeout or settings.timeout_seconds
    engine = get_engine(db_path)

    def _run():
        if settings.dialect == "postgres":
            # READ ONLY transaction + statement timeout: defence in depth.
            with engine.begin() as conn:
                conn.exec_driver_sql("SET TRANSACTION READ ONLY")
                conn.exec_driver_sql(f"SET LOCAL statement_timeout = '{int(timeout * 1000)}ms'")
                result = conn.execute(text(sql))
                cols = list(result.keys())
                rows = [list(r) for r in result.fetchmany(settings.row_limit)]
                return cols, rows
        with engine.connect() as conn:
            result = conn.execute(text(sql))
            cols = list(result.keys())
            rows = [list(r) for r in result.fetchmany(settings.row_limit)]
            return cols, rows

    start = time.time()
    with ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(_run)
        try:
            cols, rows = fut.result(timeout=timeout)
        except FuturesTimeout:
            raise TimeoutError(f"Query timed out after {timeout}s (statement timeout). Narrow the question and retry.")
        finally:
            # DuckDB: dispose the engine so its pooled read-only connection
            # is closed NOW, not when the cyclic GC gets round to it. A
            # lingering read-only connection blocks any later read-write
            # connection to the same file in this process (DuckDB forbids
            # mixed configurations) — and CSV uploads write to this file.
            # Postgres engines stay pooled (module-level cache).
            if settings.dialect != "postgres":
                engine.dispose()
    return cols, rows, int((time.time() - start) * 1000)


def reset_engine_cache() -> None:
    """Tests only: drop the cached Postgres engine."""
    global _PG_ENGINE
    _PG_ENGINE = None
