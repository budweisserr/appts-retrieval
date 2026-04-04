from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    return int(value) if value is not None else default


@dataclass(slots=True)
class Settings:
    telegram_bot_token: str
    poll_interval_seconds: int
    telegram_poll_timeout_seconds: int
    max_listings_per_search: int
    max_photos_per_message: int
    headless: bool
    seed_existing_on_start: bool
    state_db_path: Path


def load_settings() -> Settings:
    project_root = Path(__file__).resolve().parents[2]
    _load_dotenv(project_root / ".env")

    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not bot_token:
        raise ValueError("TELEGRAM_BOT_TOKEN must be set")

    state_db_path = Path(os.environ.get("STATE_DB_PATH", "data/flats.db"))
    if not state_db_path.is_absolute():
        state_db_path = project_root / state_db_path

    return Settings(
        telegram_bot_token=bot_token,
        poll_interval_seconds=_env_int("POLL_INTERVAL_SECONDS", 15),
        telegram_poll_timeout_seconds=_env_int("TELEGRAM_POLL_TIMEOUT_SECONDS", 20),
        max_listings_per_search=_env_int("MAX_LISTINGS_PER_SEARCH", 25),
        max_photos_per_message=_env_int("MAX_PHOTOS_PER_MESSAGE", 5),
        headless=_env_bool("HEADLESS", True),
        seed_existing_on_start=_env_bool("SEED_EXISTING_ON_START", True),
        state_db_path=state_db_path,
    )
