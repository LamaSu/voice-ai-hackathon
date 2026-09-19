"""Runtime configuration. All credentials come from the repo-level .env file."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BACKEND_DIR.parent

load_dotenv(REPO_DIR / ".env")
load_dotenv(BACKEND_DIR / ".env")


def _require(*names: str) -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    raise RuntimeError(f"Missing {' / '.join(names)} in {REPO_DIR / '.env'}")


@dataclass(frozen=True)
class Settings:
    gradium_api_key: str = field(default_factory=lambda: _require("GRADIUM_API_KEY"))
    general_compute_api_key: str = field(default_factory=lambda: _require("GENERAL_COMPUTE", "GENERAL_COMPUTE_API_KEY"))
    jev_api_key: str = field(default_factory=lambda: _require("JEV_API_KEY", "TYPESAFE_API_KEY"))

    general_compute_base_url: str = os.getenv(
        "GENERAL_COMPUTE_BASE_URL", "https://api.generalcompute.com/v1"
    )
    llm_model: str = os.getenv("LLM_MODEL", "minimax-m2.7")
    jev_model: str = os.getenv("JEV_MODEL", "jev-latest")
    jev_timeout_s: float = float(os.getenv("JEV_TIMEOUT_S", "0.6"))
    gradium_tts_voice: str | None = os.getenv("GRADIUM_TTS_VOICE") or None

    host: str = os.getenv("HOST", "127.0.0.1")
    port: int = int(os.getenv("PORT", "7860"))
    enable_speaker_id: bool = os.getenv("ENABLE_SPEAKER_ID", "1") == "1"


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
