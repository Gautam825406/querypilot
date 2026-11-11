"""Semantic layer loader — dbt-style YAML, Git-versioned, single source of truth."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import yaml

SEMANTIC_DIR = Path(__file__).resolve().parent.parent / "semantic"

# Canonical schema of the demo DuckDB database (also the source of truth for validation).
SCHEMA = {
    "customers": {
        "id": "INTEGER", "name": "VARCHAR", "email": "VARCHAR", "phone": "VARCHAR",
        "region": "VARCHAR", "channel": "VARCHAR", "created_at": "DATE",
    },
    "orders": {
        "id": "INTEGER", "customer_id": "INTEGER", "product_id": "INTEGER",
        "order_date": "DATE", "amount": "DECIMAL", "quantity": "INTEGER",
        "region": "VARCHAR", "channel": "VARCHAR", "status": "VARCHAR",
    },
    "products": {
        "id": "INTEGER", "name": "VARCHAR", "category": "VARCHAR", "price": "DECIMAL",
    },
    "subscriptions": {
        "id": "INTEGER", "customer_id": "INTEGER", "plan": "VARCHAR",
        "status": "VARCHAR", "started_at": "DATE", "ended_at": "DATE",
        "monthly_price": "DECIMAL",
    },
}

PII_COLUMNS = {("customers", "email"), ("customers", "phone")}


class SemanticLayer:
    def __init__(self, directory: Path = SEMANTIC_DIR):
        self.directory = directory
        self.models: dict = {}
        self.metrics: dict = {}
        self.dimensions: dict = {}
        self.joins: list = []
        self.version = "1.0.0"
        self.checksum = ""
        self.reload()

    def reload(self) -> None:
        raw = b""
        for path in sorted(self.directory.glob("*.yaml")):
            data = yaml.safe_load(path.read_text()) or {}
            raw += path.read_bytes()
            self.version = str(data.get("version", self.version))
            for m in data.get("models", []):
                self.models[m["name"]] = m
            for m in data.get("metrics", []):
                self.metrics[m["name"]] = m
            for d in data.get("dimensions", []):
                self.dimensions[d["name"]] = d
            self.joins.extend(data.get("joins", []))
        self.checksum = hashlib.sha256(raw).hexdigest()[:12]

    def metric(self, name: str):
        return self.metrics.get(name)

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "checksum": self.checksum,
            "metrics": list(self.metrics.values()),
            "dimensions": list(self.dimensions.values()),
            "models": list(self.models.values()),
            "joins": self.joins,
        }


layer = SemanticLayer()
