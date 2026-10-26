"""
Centralized, validated configuration.

Every external dependency's address lives here. Nothing is hardcoded in
modules. Values come from environment variables (loaded from .env).

Why pydantic-settings: type coercion + validation at import time. A
missing DATABASE_URL or a malformed path fails fast at startup, not at
3am during a training run. Every field is validated once, then read
from the cached singleton — zero per-request validation cost.

Usage:
    from config.settings import settings
    settings.database_url
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal, Any

from pydantic import Field, PostgresDsn, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All runtime configuration. Populated from env / .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @field_validator("database_url", mode="before")
    @classmethod
    def _normalize_db_url(cls, v: Any) -> Any:
        """
        Accept both 'postgres://' and 'postgresql://' schemes.

        Typed as Any because this is a mode='before' validator — Pydantic
        passes the raw env value (str) and we return str, which Pydantic
        then converts to PostgresDsn. Using `str` here would trigger a
        mypy "incompatible types" complaint since the field is PostgresDsn.
        """
        if isinstance(v, str) and v.startswith("postgres://"):
            return v.replace("postgres://", "postgresql://", 1)
        return v
    # ── Database ─────────────────────────────────────────────────
    # PostgresDsn validates that the scheme is postgres:// or postgresql://
    # and that the rest of the URI parses. Stringifies back to a plain URI
    # that psycopg3 accepts.
    database_url: PostgresDsn = Field(
        default="postgresql://trin:trin_dev@localhost:5432/helix_db",
        description="psycopg3 connection URI for the Helix warehouse",
    )
    db_pool_min: int = Field(default=2, ge=1, le=32)
    db_pool_max: int = Field(default=10, ge=1, le=128)

    # ── Compute ──────────────────────────────────────────────────
    # The Blackwell compute GPU sits at index 1 by design: index 0 is
    # the A2000 that drives the display, index 1 is the headless 72GB.
    cuda_visible_devices: str = Field(
        default="1",
        description="Comma-separated CUDA device indices visible to torch",
    )
    torch_dtype: Literal["bfloat16", "float16", "float32"] = "bfloat16"

    # NVFP4 training is the default on Blackwell. Falls back to BF16
    # transparently if hardware or torchao doesn't support it — see
    # engine/fp4.py for the detection logic. Setting this to false is
    # a hard override (useful for debugging numerical issues).
    use_nvfp4: bool = Field(
        default=True,
        description="Enable NVFP4 mixed-precision training (requires Blackwell + torchao 0.18+)",
    )

    # ── Paths ────────────────────────────────────────────────────
    syzygy_path: Path = Field(
        default=Path.home() / "syzygy",
        description="Root containing 3-4-5/, 6/, and/or 7/ subdirectories",
    )
    stockfish_path: Path = Field(
        default=Path.home() / "bin" / "stockfish-17.1",
        description="Stockfish binary — teacher for distillation, oracle for panel",
    )
    checkpoints_dir: Path = Field(default=Path("./checkpoints"))

    # ── External services ────────────────────────────────────────
    ollama_url: str = Field(default="http://localhost:11434")
    ollama_model: str = Field(default="deepseek-r1:32b")

    # ── Observability ────────────────────────────────────────────
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_json: bool = Field(
        default=True,
        description="JSON logs for production/aggregation; text for local dev",
    )

    # ── Capability flags ─────────────────────────────────────────
    engine_enabled: bool = Field(
        default=False,
        description="Enable Trinstinctus engine features (requires checkpoint)",
    )
    training_enabled: bool = Field(
        default=False,
        description="Enable training pipeline (requires CUDA + Blackwell for NVFP4)",
    )
    coach_llm_enabled: bool = Field(
        default=True,
        description="Enable LLM commentary in coach panel",
    )

    @field_validator("checkpoints_dir", "syzygy_path", "stockfish_path")
    @classmethod
    def _expand_user(cls, v: Path) -> Path:
        """Expand ~ and make relative paths absolute against cwd."""
        return v.expanduser().resolve() if not v.is_absolute() else v.expanduser()

    @field_validator("database_url", mode="before")
    @classmethod
    def _normalize_db_url(cls, v: str) -> str:
        """
        Accept both 'postgres://' and 'postgresql://' schemes.
        PostgresDsn accepts both but psycopg3 prefers postgresql://.
        """
        if isinstance(v, str) and v.startswith("postgres://"):
            return v.replace("postgres://", "postgresql://", 1)
        return v


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Singleton accessor. Validated once on first call.

    lru_cache ensures the settings object is created exactly once per
    process — every module that imports `settings` sees the same
    instance, so environment-variable reads (which are process-global
    anyway) are consistent across the codebase.
    """
    return Settings()


settings = get_settings()
