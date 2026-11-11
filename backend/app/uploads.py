"""CSV upload: parse a user CSV safely and store it as a real DuckDB table.

Limits (task spec): one .csv file, max 5 MB, max 100k rows, max 50 columns.
The table is named ``upl_<dataset_id>_<sanitised_stem>`` and lives in the
same DuckDB file as the demo data, so the normal read-only executor can query
it. Uploads are DuckDB-only: with a Postgres DATABASE_URL the endpoint
refuses (the Postgres target has no writable demo file to put them in).

Every generated query over an uploaded table goes through the SAME sqlglot
validator + read-only executor + correction loop as the demo metrics — the
validator allow-list is extended per-workspace (see validator.py), so a
cross-workspace table is blocked exactly like an out-of-schema table.
"""
from __future__ import annotations

import csv
import io
import re
import uuid
from datetime import date, datetime
from pathlib import Path

import duckdb

from .config import settings
from .store import delete_dataset_metadata, get_dataset, list_datasets, save_dataset

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_ROWS = 100_000
MAX_COLUMNS = 50
_SAMPLE_ROWS = 200


class UploadError(ValueError):
    """A CSV the user can fix: message is shown verbatim in the UI."""


# --------------------------------------------------------------------------
# Column-name sanitising
# --------------------------------------------------------------------------

def sanitise_name(raw: str) -> str:
    """'Total Amount ($)' -> 'total_amount'; '' -> 'col'; '2024 sales' -> 'c_2024_sales'."""
    name = re.sub(r"[^a-z0-9]+", "_", (raw or "").strip().lower()).strip("_")
    if not name:
        name = "col"
    if name[0].isdigit():
        name = "c_" + name
    return name


def sanitise_unique(headers: list[str]) -> list[str]:
    """Sanitise headers and reject duplicates after sanitising."""
    out = []
    for h in headers:
        name = sanitise_name(h)
        if name in out:
            raise UploadError(
                f"Duplicate column after sanitising: '{h}' and another column "
                f"both become '{name}'. Rename one of them and re-upload.")
        out.append(name)
    return out


# --------------------------------------------------------------------------
# Type inference
# --------------------------------------------------------------------------

_INT_RE = re.compile(r"^[+-]?\d+$")
_FLOAT_RE = re.compile(r"^[+-]?(\d+\.\d*|\.\d+|\d+)([eE][+-]?\d+)?$")
_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d", "%m/%d/%Y")


def _looks_int(v: str) -> bool:
    return bool(_INT_RE.match(v.strip()))


def _looks_float(v: str) -> bool:
    return bool(_FLOAT_RE.match(v.strip()))


def _looks_date(v: str) -> bool:
    v = v.strip()
    for fmt in _DATE_FORMATS:
        try:
            datetime.strptime(v, fmt)
            return True
        except ValueError:
            continue
    return False


def infer_type(values: list[str]) -> str:
    """Infer integer / float / date / text from a sample of non-empty values.

    A type wins when >= 90% of non-empty values parse as it; otherwise text.
    Integer is tried before float (1, 2, 3 stay integers, not 1.0, 2.0).
    """
    vals = [v for v in values if v is not None and str(v).strip() != ""]
    if not vals:
        return "text"
    n = len(vals)
    if sum(1 for v in vals if _looks_int(v)) / n >= 0.9:
        return "integer"
    if sum(1 for v in vals if _looks_float(v)) / n >= 0.9:
        return "float"
    if sum(1 for v in vals if _looks_date(v)) / n >= 0.9:
        return "date"
    return "text"


def _convert(value: str | None, col_type: str):
    """Best-effort conversion: unparseable values become NULL, never an error."""
    if value is None:
        return None
    v = str(value).strip()
    if v == "":
        return None
    try:
        if col_type == "integer":
            return int(v)
        if col_type == "float":
            return float(v)
        if col_type == "date":
            for fmt in _DATE_FORMATS:
                try:
                    return datetime.strptime(v, fmt).date()
                except ValueError:
                    continue
            return None
    except (ValueError, TypeError):
        return None
    return value


# --------------------------------------------------------------------------
# CSV parsing
# --------------------------------------------------------------------------

def parse_csv(data: bytes, filename: str) -> dict:
    """Parse + validate one CSV file. Returns {columns, rows, row_count}.

    columns: [{name (sanitised), original, type}] — types inferred from a
    sample of up to 200 data rows. rows: converted values, in column order.
    """
    if len(data) > MAX_FILE_BYTES:
        raise UploadError(
            f"File is {len(data) / 1024 / 1024:.1f} MB — the limit is 5 MB. "
            "Split the file or upload a smaller sample.")
    if not filename.lower().endswith(".csv"):
        raise UploadError("Only .csv files can be uploaded.")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    if not text.strip():
        raise UploadError("The file is empty. Upload a CSV with a header row and data rows.")
    reader = csv.reader(io.StringIO(text))
    try:
        raw_rows = list(reader)
    except csv.Error as e:
        raise UploadError(f"Could not parse the CSV: {e}")
    # Drop fully-empty trailing/leading lines (spreadsheets love them).
    raw_rows = [r for r in raw_rows if any(c.strip() for c in r)]
    if not raw_rows:
        raise UploadError("The file is empty. Upload a CSV with a header row and data rows.")
    headers = [h.strip() for h in raw_rows[0]]
    if any(not h for h in headers):
        raise UploadError("Every column needs a header name — one header cell is empty.")
    if len(headers) > MAX_COLUMNS:
        raise UploadError(f"The file has {len(headers)} columns — the limit is {MAX_COLUMNS}.")
    columns = sanitise_unique(headers)
    data_rows = raw_rows[1:]
    if not data_rows:
        raise UploadError("The file has a header but no data rows. Add at least one data row.")
    if len(data_rows) > MAX_ROWS:
        raise UploadError(f"The file has {len(data_rows):,} rows — the limit is 100,000.")
    # Normalise row width: pad short rows with empty, truncate long ones.
    normed = []
    for r in data_rows:
        r = list(r) + [""] * max(0, len(columns) - len(r))
        normed.append(r[:len(columns)])
    sample = normed[:_SAMPLE_ROWS]
    col_defs = []
    for i, name in enumerate(columns):
        col_type = infer_type([r[i] for r in sample])
        col_defs.append({"name": name, "original": headers[i], "type": col_type})
    rows = [[_convert(r[i], col_defs[i]["type"]) for i in range(len(columns))]
            for r in normed]
    return {"columns": col_defs, "rows": rows, "row_count": len(rows)}


# --------------------------------------------------------------------------
# Storage: DuckDB table + SQLite metadata
# --------------------------------------------------------------------------

_DUCKDB_TYPES = {"integer": "INTEGER", "float": "DOUBLE", "date": "DATE", "text": "VARCHAR"}


def _duckdb_write_connection():
    """A writable connection to the demo DuckDB file (the executor only ever
    opens read-only ones). Short-lived: opened, used, closed.

    DuckDB forbids a read-write connection while a read-only one to the same
    file is alive in this process. The executor disposes its engines, so in
    practice none lingers — but a genuinely concurrent ask (another thread)
    can still hold one briefly, so retry a few times before giving up.
    """
    import time as _time

    path = str(Path(settings.database_path).resolve())
    last_exc = None
    for _ in range(5):
        try:
            return duckdb.connect(path)
        except duckdb.ConnectionException as e:
            last_exc = e
            _time.sleep(0.1)
    raise last_exc


def _table_name(dataset_id: str, filename: str) -> str:
    stem = Path(filename).stem
    stem = sanitise_name(stem)[:24].strip("_") or "dataset"
    return f"upl_{dataset_id}_{stem}"


def upload_dataset(data: bytes, filename: str, workspace: str = "demo") -> dict:
    """Parse, store and register one CSV upload. Returns the dataset record
    (with example questions the UI can offer)."""
    if settings.dialect == "postgres":
        raise UploadError(
            "CSV upload needs the DuckDB demo database. The backend is "
            "currently pointed at Postgres (DATABASE_URL is set), so uploads "
            "are disabled — unset DATABASE_URL to use uploads.")
    parsed = parse_csv(data, filename)
    dataset_id = uuid.uuid4().hex[:8]
    table = _table_name(dataset_id, filename)
    columns = parsed["columns"]
    ddl_cols = ", ".join(
        f'"{c["name"]}" {_DUCKDB_TYPES[c["type"]]}' for c in columns)
    placeholders = ", ".join("?" for _ in columns)
    con = _duckdb_write_connection()
    try:
        con.execute(f'CREATE TABLE "{table}" ({ddl_cols})')
        con.executemany(f'INSERT INTO "{table}" VALUES ({placeholders})', parsed["rows"])
        con.commit()
        con.execute("CHECKPOINT")
    finally:
        con.close()
    record = {
        "id": dataset_id,
        "filename": filename,
        "table_name": table,
        "columns": columns,
        "row_count": parsed["row_count"],
        "workspace": workspace,
    }
    save_dataset(record)
    record["example_questions"] = example_questions(record)
    return record


def example_questions(ds: dict) -> list[str]:
    """2-3 real questions the user can click to ask over this dataset."""
    name = ds["table_name"].split("_", 2)[-1].replace("_", " ")
    cols = ds["columns"]
    numeric = [c for c in cols if c["type"] in ("integer", "float")]
    categorical = [c for c in cols if c["type"] == "text"]
    qs = [f"How many rows are in {name}?"]
    if numeric:
        qs.append(f"What is the total {numeric[0]['name']} in {name}?")
    if numeric and categorical:
        qs.append(f"Total {numeric[0]['name']} by {categorical[0]['name']} in {name}")
    elif numeric:
        qs.append(f"What is the average {numeric[0]['name']} in {name}?")
    return qs[:3]


def list_upload_tables(workspace: str = "demo") -> dict[str, dict[str, str]]:
    """{table_name: {column: type}} for a workspace's uploads — the extra
    allow-list the validator merges into the static schema."""
    out = {}
    for ds in list_datasets(workspace):
        out[ds["table_name"]] = {c["name"]: c["type"].upper() for c in ds["columns"]}
    return out


def delete_dataset(dataset_id: str) -> bool:
    """Drop the DuckDB table + metadata. Returns False if unknown."""
    ds = get_dataset(dataset_id)
    if not ds:
        return False
    con = _duckdb_write_connection()
    try:
        con.execute(f'DROP TABLE IF EXISTS "{ds["table_name"]}"')
        con.commit()
        con.execute("CHECKPOINT")
    finally:
        con.close()
    delete_dataset_metadata(dataset_id)
    return True
