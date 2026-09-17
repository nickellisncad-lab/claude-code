"""Durable state: which films we have already seen and already requested.

Kept in SQLite so a container restart never re-requests the whole watchlist.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    username   TEXT PRIMARY KEY,
    seeded     INTEGER NOT NULL DEFAULT 0,
    last_sync  TEXT
);

CREATE TABLE IF NOT EXISTS watchlist_seen (
    username   TEXT NOT NULL,
    slug       TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    PRIMARY KEY (username, slug)
);

-- One row per film, shared across users, so a film both people watchlist is
-- only ever requested from Radarr once.
CREATE TABLE IF NOT EXISTS films (
    slug       TEXT PRIMARY KEY,
    title      TEXT,
    year       INTEGER,
    tmdb_id    INTEGER,
    radarr_id  INTEGER,
    status     TEXT NOT NULL,
    detail     TEXT,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS films_status ON films (status);
"""

# Terminal statuses: we will not retry these films on the next pass.
DONE_STATUSES = {"added", "exists", "no_tmdb_id"}

# Non-terminal statuses: the film was seen and attempted but never actually
# reached Radarr, so it must be picked up again on the next pass even though
# it is no longer "new" to the watchlist.
RETRY_STATUSES = {"dry_run", "error", "deferred", "pending"}


class StateError(Exception):
    """The state database could not be opened."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    """SQLite-backed sync state."""

    def __init__(self, path: str) -> None:
        self.path = path
        try:
            if path != ":memory:":
                Path(path).parent.mkdir(parents=True, exist_ok=True)
            self.conn = sqlite3.connect(path)
            self.conn.row_factory = sqlite3.Row
            self.conn.executescript(_SCHEMA)
            self.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            self.conn.commit()
        except (sqlite3.OperationalError, OSError) as exc:
            # Nearly always a bind-mounted ./data owned by root while the
            # container runs unprivileged. The stack trace for this tells you
            # nothing useful, so say what it actually is and how to fix it.
            raise StateError(
                f"cannot open the state database at {path}: {exc}\n"
                f"  The container runs as uid {os.getuid()}, so the mounted "
                f"data directory must be writable by it.\n"
                f"  Fix with:  sudo chown -R {os.getuid()}:{os.getgid()} data"
            ) from exc

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    # -- users ------------------------------------------------------------

    def is_seeded(self, username: str) -> bool:
        row = self.conn.execute(
            "SELECT seeded FROM users WHERE username = ?", (username,)
        ).fetchone()
        return bool(row and row["seeded"])

    def mark_seeded(self, username: str) -> None:
        self.conn.execute(
            """
            INSERT INTO users (username, seeded, last_sync) VALUES (?, 1, ?)
            ON CONFLICT(username) DO UPDATE SET seeded = 1, last_sync = excluded.last_sync
            """,
            (username, _now()),
        )
        self.conn.commit()

    # -- watchlist membership --------------------------------------------

    def seen_slugs(self, username: str) -> set[str]:
        rows = self.conn.execute(
            "SELECT slug FROM watchlist_seen WHERE username = ?", (username,)
        )
        return {row["slug"] for row in rows}

    def record_seen(self, username: str, slugs: list[str]) -> None:
        now = _now()
        self.conn.executemany(
            """
            INSERT INTO watchlist_seen (username, slug, first_seen) VALUES (?, ?, ?)
            ON CONFLICT(username, slug) DO NOTHING
            """,
            [(username, slug, now) for slug in slugs],
        )
        self.conn.commit()

    def forget_seen(self, username: str, slugs: list[str]) -> None:
        """Drop entries the user has removed from their watchlist."""
        self.conn.executemany(
            "DELETE FROM watchlist_seen WHERE username = ? AND slug = ?",
            [(username, slug) for slug in slugs],
        )
        self.conn.commit()

    # -- films ------------------------------------------------------------

    def film_status(self, slug: str) -> str | None:
        row = self.conn.execute(
            "SELECT status FROM films WHERE slug = ?", (slug,)
        ).fetchone()
        return row["status"] if row else None

    def is_done(self, slug: str) -> bool:
        return self.film_status(slug) in DONE_STATUSES

    def record_film(
        self,
        slug: str,
        *,
        status: str,
        title: str | None = None,
        year: int | None = None,
        tmdb_id: int | None = None,
        radarr_id: int | None = None,
        detail: str | None = None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO films (slug, title, year, tmdb_id, radarr_id, status, detail, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(slug) DO UPDATE SET
                title      = COALESCE(excluded.title, films.title),
                year       = COALESCE(excluded.year, films.year),
                tmdb_id    = COALESCE(excluded.tmdb_id, films.tmdb_id),
                radarr_id  = COALESCE(excluded.radarr_id, films.radarr_id),
                status     = excluded.status,
                detail     = excluded.detail,
                updated_at = excluded.updated_at
            """,
            (slug, title, year, tmdb_id, radarr_id, status, detail, _now()),
        )
        self.conn.commit()

    def retryable_slugs(self) -> set[str]:
        """Slugs that were attempted but did not land in Radarr."""
        placeholders = ",".join("?" * len(RETRY_STATUSES))
        rows = self.conn.execute(
            f"SELECT slug FROM films WHERE status IN ({placeholders})",
            tuple(sorted(RETRY_STATUSES)),
        )
        return {row["slug"] for row in rows}

    def counts_by_status(self) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT status, COUNT(*) AS n FROM films GROUP BY status"
        )
        return {row["status"]: row["n"] for row in rows}
