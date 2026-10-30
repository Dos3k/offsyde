"""Runtime configuration, read from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    db_url: str = "sqlite:///data/offsyde.db"
    raw_dir: Path = Path("data/raw")
    ollama_host: str = "http://localhost:11434"
    model: str = "qwen2.5-coder:7b"
    # Ollama's default context window is small and silently truncates long prompts.
    num_ctx: int = 8192
    llm_timeout_s: float = 180.0
    max_rows: int = 200
    query_timeout_s: float = 10.0
    max_repair_attempts: int = 2
    rate_limit_per_min: int = 30

    @classmethod
    def from_env(cls) -> Settings:
        d = cls()
        return cls(
            db_url=os.getenv("OFFSYDE_DB_URL", d.db_url),
            raw_dir=Path(os.getenv("OFFSYDE_RAW_DIR", str(d.raw_dir))),
            ollama_host=os.getenv("OLLAMA_HOST", d.ollama_host).rstrip("/"),
            model=os.getenv("OFFSYDE_MODEL", d.model),
            num_ctx=int(os.getenv("OFFSYDE_NUM_CTX", d.num_ctx)),
            llm_timeout_s=float(os.getenv("OFFSYDE_LLM_TIMEOUT", d.llm_timeout_s)),
            max_rows=int(os.getenv("OFFSYDE_MAX_ROWS", d.max_rows)),
            query_timeout_s=float(os.getenv("OFFSYDE_QUERY_TIMEOUT", d.query_timeout_s)),
            max_repair_attempts=int(os.getenv("OFFSYDE_MAX_REPAIRS", d.max_repair_attempts)),
            rate_limit_per_min=int(os.getenv("OFFSYDE_RATE_LIMIT", d.rate_limit_per_min)),
        )
