"""QueryPilot configuration — env names only, no secrets in code."""
import os
from dataclasses import dataclass, field
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parent.parent


def _resolve(p: str) -> str:
    path = Path(p)
    return str(path if path.is_absolute() else _BACKEND_DIR / path)


@dataclass
class Settings:
    database_path: str = field(default_factory=lambda: _resolve(
        os.environ.get("DEMO_DUCKDB_PATH", "data/demo.duckdb")))
    audit_db_path: str = field(default_factory=lambda: _resolve(
        os.environ.get("AUDIT_DB_PATH", "querypilot.db")))
    layer_version: str = "1.0.0"
    row_limit: int = int(os.environ.get("QP_ROW_LIMIT", "100"))
    timeout_seconds: int = int(os.environ.get("QP_TIMEOUT_SECONDS", "10"))
    cache_ttl_seconds: int = int(os.environ.get("QP_CACHE_TTL", "300"))
    max_correction_attempts: int = 3
    groq_api_key: str = os.environ.get("GROQ_API_KEY", "")
    gemini_api_key: str = os.environ.get("GEMINI_API_KEY", "")
    openai_api_key: str = os.environ.get("OPENAI_API_KEY", "")

    @property
    def llm_mode(self) -> str:
        if self.groq_api_key:
            return "groq"
        if self.gemini_api_key:
            return "gemini"
        if self.openai_api_key:
            return "openai"
        return "deterministic-fallback"


settings = Settings()
