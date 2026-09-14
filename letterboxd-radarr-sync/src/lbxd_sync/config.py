"""Configuration, loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

DEFAULT_USER_AGENT = (
    "letterboxd-radarr-sync/1.0 (+https://github.com/nickellisncad-lab/claude-code)"
)


class ConfigError(Exception):
    """Raised when the environment is missing or has malformed settings."""


def _str(name: str, default: str | None = None, *, required: bool = False) -> str:
    value = os.environ.get(name, default if default is not None else "").strip()
    if required and not value:
        raise ConfigError(f"{name} is required but not set")
    return value


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc


def _float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from exc


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "y", "on"}:
        return True
    if raw in {"0", "false", "no", "n", "off"}:
        return False
    raise ConfigError(f"{name} must be a boolean, got {raw!r}")


def _list(name: str, default: str = "") -> list[str]:
    raw = os.environ.get(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


# Radarr only accepts these values for minimumAvailability.
VALID_AVAILABILITY = {"announced", "inCinemas", "released"}


@dataclass(frozen=True)
class Config:
    """Everything the sync needs to run."""

    letterboxd_users: list[str]
    radarr_url: str
    radarr_api_key: str
    root_folder: str
    quality_profile: str
    minimum_availability: str = "released"
    monitor: bool = True
    search_on_add: bool = True
    tags: list[str] = field(default_factory=list)

    poll_interval: int = 3600
    state_path: str = "/data/state.db"
    seed_on_first_run: bool = True
    dry_run: bool = False

    request_delay: float = 1.0
    request_timeout: float = 30.0
    max_retries: int = 3
    user_agent: str = DEFAULT_USER_AGENT
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> "Config":
        users = _list("LETTERBOXD_USERS")
        if not users:
            raise ConfigError(
                "LETTERBOXD_USERS is required (comma-separated Letterboxd usernames)"
            )

        # Usernames are interpolated straight into URLs; keep them to the
        # character set Letterboxd actually allows so a stray value cannot
        # walk out of the /<user>/watchlist/ path.
        for user in users:
            if not all(ch.isalnum() or ch in "_-" for ch in user):
                raise ConfigError(f"invalid Letterboxd username: {user!r}")

        availability = _str("RADARR_MINIMUM_AVAILABILITY", "released")
        if availability not in VALID_AVAILABILITY:
            raise ConfigError(
                f"RADARR_MINIMUM_AVAILABILITY must be one of "
                f"{sorted(VALID_AVAILABILITY)}, got {availability!r}"
            )

        interval = _int("POLL_INTERVAL", 3600)
        if interval < 60:
            raise ConfigError("POLL_INTERVAL must be at least 60 seconds")

        return cls(
            letterboxd_users=users,
            radarr_url=_str("RADARR_URL", required=True).rstrip("/"),
            radarr_api_key=_str("RADARR_API_KEY", required=True),
            root_folder=_str("RADARR_ROOT_FOLDER", required=True),
            quality_profile=_str("RADARR_QUALITY_PROFILE", required=True),
            minimum_availability=availability,
            monitor=_bool("RADARR_MONITOR", True),
            search_on_add=_bool("RADARR_SEARCH_ON_ADD", True),
            tags=_list("RADARR_TAGS"),
            poll_interval=interval,
            state_path=_str("STATE_PATH", "/data/state.db"),
            seed_on_first_run=_bool("SEED_ON_FIRST_RUN", True),
            dry_run=_bool("DRY_RUN", False),
            request_delay=_float("REQUEST_DELAY", 1.0),
            request_timeout=_float("REQUEST_TIMEOUT", 30.0),
            max_retries=_int("MAX_RETRIES", 3),
            user_agent=_str("USER_AGENT", DEFAULT_USER_AGENT),
            log_level=_str("LOG_LEVEL", "INFO").upper(),
        )
