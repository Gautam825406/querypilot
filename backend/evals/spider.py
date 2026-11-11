"""Spider benchmark subset runner (PRD §13.1) — attempt + honest report.

What this does:
  1. Fetches the first N validation examples (default 50, file order) from the
     public HuggingFace copy of Spider (xlangai/spider) via the datasets-server
     API — no `datasets` install needed.
  2. Attempts to fetch the sqlite database for each example's db_id from the
     known directly-downloadable locations (taoyds/spider and
     taoyds/test-suite-sql-eval on GitHub).
  3. Runs execution accuracy ONLY if (a) the databases are available AND
     (b) an LLM key is set — Spider schemas are arbitrary and outside the demo
     semantic layer, so general text-to-SQL needs the direct-LLM path.

What it does NOT do: invent a Spider score. If any step is infeasible, the
report says so, with the exact reason, and no accuracy number is claimed.
Run:  python backend/evals/spider.py [--limit 50]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.http import get as http_get, stream_get  # noqa: E402

HF_ROWS = "https://datasets-server.huggingface.co/rows"
DATASET = "xlangai/spider"
DB_URLS = [
    "https://raw.githubusercontent.com/taoyds/spider/master/database/{db}/{db}.sqlite",
    "https://raw.githubusercontent.com/taoyds/test-suite-sql-eval/master/database/{db}/{db}.sqlite",
]
OUT = Path(__file__).parent / "spider_report.json"


def _fetch_examples(limit: int):
    """First `limit` validation examples, in file order (documented selection)."""
    rows = []
    offset = 0
    while len(rows) < limit:
        r = http_get(HF_ROWS, params={"dataset": DATASET, "config": "spider",
                                       "split": "validation", "offset": offset,
                                       "length": min(100, limit - len(rows))},
                      timeout=30)
        r.raise_for_status()
        data = r.json()
        batch = data.get("rows", [])
        if not batch:
            break
        rows.extend({"db_id": row["row"]["db_id"],
                     "question": row["row"]["question"],
                     "query": row["row"]["query"]} for row in batch)
        offset += len(batch)
    return rows[:limit]


def _fetch_db(db_id: str, dest_dir: Path) -> str | None:
    """Try each known direct location. Returns the local path, or None."""
    dest = dest_dir / f"{db_id}.sqlite"
    if dest.exists() and dest.stat().st_size > 0:
        return str(dest)
    for template in DB_URLS:
        url = template.format(db=db_id)
        try:
            with stream_get(url, timeout=30) as r:
                if r.status_code != 200:
                    continue
                size = 0
                with open(dest, "wb") as f:
                    for chunk in r.iter_bytes(1 << 16):
                        f.write(chunk)
                        size += len(chunk)
                        if size > 50 * 1024 * 1024:  # 50 MB cap per DB
                            raise ValueError("DB larger than 50 MB cap")
            if dest.stat().st_size < 1024:  # an error page, not a database
                dest.unlink(missing_ok=True)
                continue
            return str(dest)
        except Exception:
            dest.unlink(missing_ok=True)
            continue
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=50)
    args = ap.parse_args()

    report: dict = {"dataset": "Spider (xlangai/spider, validation split)",
                    "selection": f"first {args.limit} examples in file order",
                    "limit": args.limit, "status": "not run", "reason": "",
                    "examples_fetched": 0, "databases_available": 0,
                    "execution_accuracy": None, "per_case": []}
    started = time.time()
    try:
        examples = _fetch_examples(args.limit)
    except Exception as e:
        report["reason"] = f"Could not fetch Spider examples from HuggingFace: {e}"
        OUT.write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))
        return report
    report["examples_fetched"] = len(examples)

    db_dir = Path(__file__).parent / "spider_db"
    db_dir.mkdir(exist_ok=True)
    needed = sorted({ex["db_id"] for ex in examples})
    available = {}
    for db_id in needed:
        path = _fetch_db(db_id, db_dir)
        if path:
            available[db_id] = path
    report["databases_available"] = len(available)
    report["databases_needed"] = needed
    if len(available) < len(needed):
        report["reason"] = (
            "Spider sqlite databases are not directly downloadable: the "
            "taoyds/spider and taoyds/test-suite-sql-eval GitHub repos do not "
            "host the database files (they are distributed via Google Drive), "
            "so execution accuracy cannot be measured without them.")
        OUT.write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))
        return report

    from app.config import settings
    if not settings.groq_api_key:
        report["reason"] = (
            "Databases are available, but Spider questions use arbitrary "
            "schemas outside the demo semantic layer, so they need the "
            "direct-LLM text-to-SQL path — and no LLM key (GROQ_API_KEY) is "
            "set. No Spider score is claimed without running it.")
        OUT.write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))
        return report

    # Databases available AND an LLM key is set: run execution accuracy.
    from evals.run_eval import _baseline_generate, _norm
    correct = 0
    per_case = []
    for ex in examples:
        sql = _baseline_generate(ex["question"])
        passed = False
        try:
            con = sqlite3.connect(available[ex["db_id"]])
            gold_rows = _norm(con.execute(ex["query"]).fetchall())
            if sql:
                got_rows = _norm(con.execute(sql).fetchall())
                passed = got_rows == gold_rows
            con.close()
        except Exception:
            passed = False
        correct += 1 if passed else 0
        per_case.append({"db_id": ex["db_id"], "question": ex["question"],
                         "passed": passed})
    report.update({"status": "run",
                   "execution_accuracy": round(correct / max(len(examples), 1), 4),
                   "correct": correct, "total": len(examples),
                   "reason": "", "per_case": per_case,
                   "duration_seconds": round(time.time() - started, 1)})
    OUT.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    main()
