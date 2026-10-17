"""Runtime configuration, read from environment variables (see .env.example)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _list(name: str, default: str) -> list[str]:
    return [x.strip() for x in os.getenv(name, default).split(",") if x.strip()]


@dataclass(frozen=True)
class Settings:
    # LLM: "gemini" (AI Studio key), "vertex" (GCP service account) or "offline"
    llm_provider: str = field(default_factory=lambda: os.getenv("LLM_PROVIDER", "gemini"))
    gemini_model: str = field(default_factory=lambda: os.getenv("GEMINI_MODEL", "gemini-3.8-flash"))
    # Slot extraction + safety review run on a cheaper model (separate per-model quota).
    # Set EXTRACTION_MODEL= (empty) to reuse GEMINI_MODEL.
    extraction_model: str = field(default_factory=lambda: os.getenv("EXTRACTION_MODEL", "gemini-3.5-flash-lite")
                                  or os.getenv("GEMINI_MODEL", "gemini-3.8-flash"))
    google_api_key: str = field(default_factory=lambda: os.getenv("GOOGLE_API_KEY", ""))
    gcp_project: str = field(default_factory=lambda: os.getenv("GOOGLE_CLOUD_PROJECT", ""))
    gcp_location: str = field(default_factory=lambda: os.getenv("GOOGLE_CLOUD_LOCATION", "asia-south1"))
    temperature: float = field(default_factory=lambda: float(os.getenv("LLM_TEMPERATURE", "0.6")))
    # Per-call deadline: a slow call falls back (heuristics / templates) instead of stalling the chat.
    llm_timeout: float = field(default_factory=lambda: float(os.getenv("LLM_TIMEOUT_SECONDS", "20")))
    llm_max_retries: int = field(default_factory=lambda: int(os.getenv("LLM_MAX_RETRIES", "1")))

    # Microservices
    emotion_service_url: str = field(default_factory=lambda: os.getenv("EMOTION_SERVICE_URL", "http://127.0.0.1:8081"))
    service_key: str = field(default_factory=lambda: os.getenv("SERVICE_KEY", ""))

    # Storage: "sqlite" for local dev, "firestore" on GCP
    storage_backend: str = field(default_factory=lambda: os.getenv("STORAGE_BACKEND", "sqlite"))
    sqlite_path: str = field(default_factory=lambda: os.getenv("SQLITE_PATH", "arnaai.db"))
    data_encryption_key: str = field(default_factory=lambda: os.getenv("DATA_ENCRYPTION_KEY", ""))

    # Human-in-the-loop
    counselor_token: str = field(default_factory=lambda: os.getenv("COUNSELOR_TOKEN", ""))
    escalation_webhook_url: str = field(default_factory=lambda: os.getenv("ESCALATION_WEBHOOK_URL", ""))

    allowed_origins: list[str] = field(default_factory=lambda: _list("ALLOWED_ORIGINS", "http://localhost:3000"))
    rate_limit_per_minute: int = field(default_factory=lambda: int(os.getenv("RATE_LIMIT_PER_MINUTE", "30")))
    history_window: int = field(default_factory=lambda: int(os.getenv("HISTORY_WINDOW", "12")))


settings = Settings()
