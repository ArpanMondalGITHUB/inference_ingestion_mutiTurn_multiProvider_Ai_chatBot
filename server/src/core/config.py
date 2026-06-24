import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

def _clean_env(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None

    value = value.strip()
    return value or None


def _get_csv_env(name: str) -> list[str]:
    value = _clean_env(name)
    if not value:
        return []

    return [item.strip().rstrip("/") for item in value.split(",") if item.strip()]


def _get_bool_env(name: str, default: bool = True) -> bool:
    value = _clean_env(name)
    if value is None:
        return default

    return value.lower() not in {"0", "false", "no", "off"}


def _get_int_env(name: str, default: int) -> int:
    value = _clean_env(name)
    if value is None:
        return default

    try:
        return int(value)
    except ValueError:
        return default


def _models_with_default(models: list[str], default_model: str) -> list[str]:
    if default_model in models:
        return models

    return [default_model, *models]


FRONTEND_URL = _clean_env("FRONTEND_URL")


BACKEND_URL = _clean_env("BACKEND_URL")

DATABASE_PATH = _clean_env("DATABASE_PATH")

AI_DEFAULT_PROVIDER = _clean_env("AI_DEFAULT_PROVIDER") or "gemini"

GEMINI_API_KEY = _clean_env("GEMINI_API_KEY")
OPENAI_API_KEY = _clean_env("OPENAI_API_KEY") or _clean_env("OPEN_AI_API_KEY")
ANTHROPIC_API_KEY = _clean_env("ANTHROPIC_API_KEY")

GEMINI_MODEL = _clean_env("GEMINI_MODEL") or "gemini-3.5-flash"
OPENAI_MODEL = (
    _clean_env("OPENAI_MODEL")
    or _clean_env("OPEN_AI_MODEL")
    or _clean_env("OPAN_AI_MODEL")
    or "gpt-4.1"
)
ANTHROPIC_MODEL = _clean_env("ANTHROPIC_MODEL") or "claude-opus-4-6"

GEMINI_MODELS = _models_with_default(_get_csv_env("GEMINI_MODELS"), GEMINI_MODEL)
OPENAI_MODELS = _models_with_default(_get_csv_env("OPENAI_MODELS"), OPENAI_MODEL)
ANTHROPIC_MODELS = _models_with_default(
    _get_csv_env("ANTHROPIC_MODELS"),
    ANTHROPIC_MODEL,
)

LLM_INGESTION_URL = _clean_env("LLM_INGESTION_URL")

LOG_INGESTION_KEY = _clean_env("LOG_INGESTION_KEY")

LLM_LOGGING_ENABLED = _get_bool_env("LLM_LOGGING_ENABLED")

MAX_EVENTS_PER_REQUEST = _get_int_env("MAX_EVENTS_PER_REQUEST", 100)

MAX_INGESTION_BODY_BYTES = _get_int_env("MAX_INGESTION_BODY_BYTES", 2 * 1024 * 1024)

MAX_CONTEXT_MESSAGES = _get_int_env("MAX_CONTEXT_MESSAGES", 8)

CORS_ORIGINS = _get_csv_env("CORS_ORIGINS")
if not CORS_ORIGINS and FRONTEND_URL:
    CORS_ORIGINS = [FRONTEND_URL.rstrip("/")]
