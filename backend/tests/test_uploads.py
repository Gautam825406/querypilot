"""Tests for CSV upload + generic uploaded-data questions (Feature 2).

- Upload happy path + bad CSV rejected (empty, header-only, empty header,
  duplicate-after-sanitise, oversize, too many columns, too many rows)
- Type inference
- Validator allows the uploaded table for its workspace, blocks other workspaces
- Generic planner: sum / count / group-by / average / top-N / show rows over
  an uploaded table, ambiguous -> clarifying, unanswerable -> honest refusal
- Delete drops the DuckDB table + metadata

Run: python -m pytest backend/tests -q (from repo root). Tests use the real
demo DuckDB file like the rest of the suite; every upload is deleted in a
finally block so later tests (and later runs) start clean.
"""
import sys
from pathlib import Path

import duckdb
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402
from app.planner import run_ask  # noqa: E402
from app.store import get_dataset, list_datasets  # noqa: E402
from app.uploads import UploadError, delete_dataset, infer_type, parse_csv, upload_dataset  # noqa: E402
from app.validator import ValidationError, validate_sql  # noqa: E402

SAMPLE = (b"date,region,product,amount,quantity\n"
          b"2025-01-03,North,Widget A,120.50,2\n"
          b"2025-01-04,South,Widget B,75.00,1\n"
          b"2025-02-10,North,Widget A,200.00,3\n"
          b"2025-02-11,East,Widget C,50.25,1\n")

FILENAME = "sample-sales.csv"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _upload(client, filename=FILENAME, content=SAMPLE):
    r = client.post("/api/data/upload", files={"file": (filename, content, "text/csv")})
    assert r.status_code == 200, r.text
    return r.json()


def _table(ds):
    return ds["table_name"]


# ---------- Parsing + type inference ----------

def test_parse_csv_happy_path():
    out = parse_csv(SAMPLE, FILENAME)
    assert out["row_count"] == 4
    assert [(c["name"], c["type"]) for c in out["columns"]] == [
        ("date", "date"), ("region", "text"), ("product", "text"),
        ("amount", "float"), ("quantity", "integer")]
    assert out["rows"][0][3] == 120.5
    assert out["rows"][0][4] == 2


def test_infer_type():
    assert infer_type(["1", "2", "3"]) == "integer"
    assert infer_type(["1.5", "2", "3.25"]) == "float"
    assert infer_type(["2025-01-03", "2025-02-10"]) == "date"
    assert infer_type(["North", "South"]) == "text"
    assert infer_type(["1", "x", "3"]) == "text"  # mixed -> text, never a crash
    assert infer_type(["", "  "]) == "text"


def test_parse_csv_rejects_empty():
    with pytest.raises(UploadError, match="empty"):
        parse_csv(b"", "empty.csv")
    with pytest.raises(UploadError, match="empty"):
        parse_csv(b"   \n\n", "empty.csv")


def test_parse_csv_rejects_header_only():
    with pytest.raises(UploadError, match="no data rows"):
        parse_csv(b"date,region,amount\n", "headers.csv")


def test_parse_csv_rejects_empty_header_cell():
    with pytest.raises(UploadError, match="header"):
        parse_csv(b"date,,amount\n2025-01-01,x,1\n", "bad.csv")


def test_parse_csv_rejects_duplicate_after_sanitise():
    with pytest.raises(UploadError, match="Duplicate column"):
        parse_csv(b"Amount,amount\n1,2\n", "dup.csv")
    with pytest.raises(UploadError, match="Duplicate column"):
        parse_csv(b"Total Amount ($),total amount\n1,2\n", "dup2.csv")


def test_parse_csv_rejects_oversize():
    with pytest.raises(UploadError, match="5 MB"):
        parse_csv(b"a,b\n" + b"1,2\n" * 2000000, "big.csv")


def test_parse_csv_rejects_too_many_columns():
    header = ",".join(f"c{i}" for i in range(51))
    with pytest.raises(UploadError, match="50"):
        parse_csv((header + "\n1" + ",1" * 50 + "\n").encode(), "wide.csv")


def test_parse_csv_rejects_too_many_rows():
    data = b"a\n" + b"1\n" * 100001
    with pytest.raises(UploadError, match="100,000"):
        parse_csv(data, "many.csv")


def test_parse_csv_rejects_non_csv_extension():
    with pytest.raises(UploadError, match=".csv"):
        parse_csv(SAMPLE, "data.txt")


# ---------- Upload endpoint ----------

def test_upload_happy_path_lists_and_deletes(client):
    ds = _upload(client)
    try:
        assert ds["row_count"] == 4
        assert ds["filename"] == FILENAME
        assert ds["table_name"].startswith("upl_")
        assert ds["example_questions"], "upload should suggest example questions"
        r = client.get("/api/data/datasets")
        assert r.status_code == 200
        listed = [d for d in r.json()["datasets"] if d["id"] == ds["id"]]
        assert len(listed) == 1
        assert listed[0]["row_count"] == 4
        assert [c["name"] for c in listed[0]["columns"]] == [
            "date", "region", "product", "amount", "quantity"]
        # The table is a real DuckDB table with the uploaded rows.
        con = duckdb.connect(str(Path(settings.database_path).resolve()), read_only=True)
        n = con.execute(f'SELECT COUNT(*) FROM "{ds["table_name"]}"').fetchone()[0]
        con.close()
        assert n == 4
    finally:
        r = client.delete(f"/api/data/datasets/{ds['id']}")
        assert r.status_code == 200
    assert get_dataset(ds["id"]) is None
    r = client.get("/api/data/datasets")
    assert all(d["id"] != ds["id"] for d in r.json()["datasets"])
    # The DuckDB table is gone too, not just the metadata.
    con = duckdb.connect(str(Path(settings.database_path).resolve()), read_only=True)
    with pytest.raises(Exception):
        con.execute(f'SELECT COUNT(*) FROM "{ds["table_name"]}"')
    con.close()


def test_upload_bad_csv_rejected_via_api(client):
    r = client.post("/api/data/upload", files={"file": ("empty.csv", b"", "text/csv")})
    assert r.status_code == 400
    r = client.post("/api/data/upload", files={"file": ("data.txt", SAMPLE, "text/plain")})
    assert r.status_code == 400
    r = client.post("/api/data/upload",
                    files={"file": ("dup.csv", b"Amount,amount\n1,2\n", "text/csv")})
    assert r.status_code == 400
    assert "Duplicate column" in r.text


def test_delete_unknown_dataset_404(client):
    assert client.delete("/api/data/datasets/doesnotexist").status_code == 404


# ---------- Validator scoping ----------

def test_validator_allows_own_workspace_table_blocks_others(client):
    ds = _upload(client)
    try:
        sql = f'SELECT SUM(amount) AS total_amount FROM "{ds["table_name"]}"'
        out = validate_sql(sql, workspace="demo")
        assert out["ok"] is True
        with pytest.raises(ValidationError) as e:
            validate_sql(sql, workspace="someone-elses-workspace")
        assert e.value.rule == "schema"
    finally:
        client.delete(f"/api/data/datasets/{ds['id']}")


def test_upload_workspace_isolation_via_store():
    ds = upload_dataset(SAMPLE, FILENAME, workspace="ws2")
    try:
        assert all(d["id"] != ds["id"] for d in list_datasets("demo"))
        assert any(d["id"] == ds["id"] for d in list_datasets("ws2"))
        sql = f'SELECT COUNT(*) FROM "{ds["table_name"]}"'
        with pytest.raises(ValidationError):
            validate_sql(sql, workspace="demo")
        assert validate_sql(sql, workspace="ws2")["ok"] is True
    finally:
        delete_dataset(ds["id"])


# ---------- Generic planner over an uploaded table ----------

def test_planner_sum_count_groupby_average(client):
    ds = _upload(client)
    try:
        total = run_ask("What is the total amount in sample sales?")
        assert total["status"] == "executed", total
        assert total["rows"][0][0] == pytest.approx(445.75)
        assert total["dataset"] == "sample sales"

        count = run_ask("How many rows are in sample sales?")
        assert count["status"] == "executed"
        assert count["rows"][0][0] == 4

        avg = run_ask("What is the average amount in sample sales?")
        assert avg["status"] == "executed"
        assert avg["rows"][0][0] == pytest.approx(445.75 / 4)

        by_region = run_ask("Total amount by region in sample sales")
        assert by_region["status"] == "executed"
        rows = {r[0]: r[1] for r in by_region["rows"]}
        assert rows == {"North": pytest.approx(320.50), "South": pytest.approx(75.00),
                        "East": pytest.approx(50.25)}

        top = run_ask("Top 2 amount in sample sales")
        assert top["status"] == "executed"
        assert len(top["rows"]) == 2
        assert top["rows"][0][3] == pytest.approx(200.00)

        shown = run_ask("Show rows in sample sales")
        assert shown["status"] == "executed"
        assert len(shown["rows"]) == 4
    finally:
        client.delete(f"/api/data/datasets/{ds['id']}")


def test_planner_ambiguous_column_asks_for_clarification(client):
    ds = _upload(client)
    try:
        run = run_ask("What is the total in sample sales?")
        assert run["status"] == "clarifying"
        assert "amount" in run["clarifying_question"] or "amount" in " ".join(run["clarifying_options"])
        assert len(run["clarifying_options"]) == 2
    finally:
        client.delete(f"/api/data/datasets/{ds['id']}")


def test_planner_unanswerable_upload_question_refuses_honestly(client):
    ds = _upload(client)
    try:
        run = run_ask("What is the profit in sample sales?")
        assert run["status"] == "refused"
        assert "amount" in run["refusal"] and "region" in run["refusal"]
    finally:
        client.delete(f"/api/data/datasets/{ds['id']}")


def test_demo_questions_unaffected_by_upload(client):
    ds = _upload(client)
    try:
        run = run_ask("What is total revenue?")
        assert run["status"] == "executed"
        assert run["rows"][0][0] == pytest.approx(517171.84)
    finally:
        client.delete(f"/api/data/datasets/{ds['id']}")
