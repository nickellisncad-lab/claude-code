"""One sync pass: watchlists in, Radarr requests out."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .config import Config
from .letterboxd import Film, LetterboxdClient, LetterboxdError, WatchlistUnavailable
from .radarr import MovieAlreadyExists, RadarrClient, RadarrError
from .state import Store

log = logging.getLogger(__name__)


@dataclass
class SyncSummary:
    """What one pass did, for logging and for the --once exit code."""

    seeded_users: list[str] = field(default_factory=list)
    new_films: int = 0
    added: int = 0
    already_in_radarr: int = 0
    skipped_no_tmdb: int = 0
    failed: int = 0
    user_errors: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.user_errors and self.failed == 0

    def describe(self) -> str:
        parts = [
            f"{self.new_films} new",
            f"{self.added} added",
            f"{self.already_in_radarr} already in Radarr",
        ]
        if self.skipped_no_tmdb:
            parts.append(f"{self.skipped_no_tmdb} skipped (no TMDB id)")
        if self.failed:
            parts.append(f"{self.failed} failed")
        if self.user_errors:
            parts.append(f"{len(self.user_errors)} watchlist errors")
        return ", ".join(parts)


class Syncer:
    """Wires the Letterboxd scraper, the state store, and Radarr together."""

    def __init__(
        self,
        config: Config,
        letterboxd: LetterboxdClient,
        radarr: RadarrClient,
        store: Store,
    ) -> None:
        self.config = config
        self.letterboxd = letterboxd
        self.radarr = radarr
        self.store = store
        self._quality_profile_id: int | None = None
        self._tag_ids: list[int] | None = None

    def preflight(self) -> None:
        """Validate the Radarr side before touching any watchlist.

        Better to fail on startup with a clear message than to scrape two
        watchlists and then discover the quality profile name is wrong.
        """
        status = self.radarr.system_status()
        log.info("connected to Radarr %s", status.get("version", "?"))

        self.radarr.validate_root_folder(self.config.root_folder)
        self._quality_profile_id = self.radarr.resolve_quality_profile(
            self.config.quality_profile
        )
        log.info(
            "using quality profile %r (id %d), root folder %s",
            self.config.quality_profile,
            self._quality_profile_id,
            self.config.root_folder,
        )

        if self.config.dry_run:
            # Creating tags is a write; skip it when we promised not to write.
            self._tag_ids = []
            if self.config.tags:
                log.info("dry run: not creating tags %s", self.config.tags)
        else:
            self._tag_ids = self.radarr.ensure_tags(self.config.tags)

    def run_once(self) -> SyncSummary:
        """Scrape every configured watchlist and request anything new."""
        summary = SyncSummary()

        if self._quality_profile_id is None:
            self.preflight()

        # Collect first, then act, so one film wanted by both users is only
        # requested once and is attributed to whoever we saw it from first.
        pending: dict[str, Film] = {}

        for username in self.config.letterboxd_users:
            try:
                new_films = self._collect_new_films(username, summary)
            except (LetterboxdError, WatchlistUnavailable) as exc:
                log.error("%s: %s", username, exc)
                summary.user_errors[username] = str(exc)
                continue

            for film in new_films:
                pending.setdefault(film.slug, film)

        summary.new_films = len(pending)
        if not pending:
            log.info("no new watchlist entries")
            return summary

        for film in pending.values():
            self._request_film(film, summary)

        return summary

    def _collect_new_films(self, username: str, summary: SyncSummary) -> list[Film]:
        """Return watchlist entries for ``username`` we have not handled yet."""
        watchlist = self.letterboxd.fetch_watchlist(username)
        current = {film.slug: film for film in watchlist.films}
        log.info("%s: watchlist has %d films", username, len(current))

        previously_seen = self.store.seen_slugs(username)
        new_slugs = [slug for slug in current if slug not in previously_seen]

        # First time we have ever looked at this user: record the whole list as
        # the baseline instead of dumping every film they have ever saved into
        # Radarr. Only additions from here on count as new.
        if not self.store.is_seeded(username):
            if self.config.seed_on_first_run:
                self.store.record_seen(username, list(current))
                self.store.mark_seeded(username)
                summary.seeded_users.append(username)
                log.info(
                    "%s: first run, recorded %d existing films as the baseline "
                    "(set SEED_ON_FIRST_RUN=false to import them instead)",
                    username,
                    len(current),
                )
                return []
            log.info(
                "%s: first run, importing all %d films (SEED_ON_FIRST_RUN=false)",
                username,
                len(current),
            )
            self.store.mark_seeded(username)

        # Drop entries the user has removed, so a re-add is noticed again.
        removed = [slug for slug in previously_seen if slug not in current]
        if removed:
            self.store.forget_seen(username, removed)
            log.info("%s: %d films removed from watchlist", username, len(removed))

        self.store.record_seen(username, new_slugs)

        fresh = [current[slug] for slug in new_slugs if not self.store.is_done(slug)]

        # Films we saw before but never got into Radarr -- a dry run, or a
        # transient Radarr or network error. They are no longer "new", so
        # without this they would be skipped forever.
        retryable = self.store.retryable_slugs()
        retries = [
            current[slug]
            for slug in current
            if slug not in new_slugs and slug in retryable
        ]

        if new_slugs:
            log.info(
                "%s: %d new entries (%d need requesting)",
                username,
                len(new_slugs),
                len(fresh),
            )
        if retries:
            log.info("%s: retrying %d previously unresolved films", username, len(retries))
        return fresh + retries

    def _request_film(self, film: Film, summary: SyncSummary) -> None:
        """Resolve one film to a TMDB id and hand it to Radarr."""
        try:
            tmdb_id = self.letterboxd.fetch_tmdb_id(film.slug)
        except LetterboxdError as exc:
            log.error("%s: could not load film page: %s", film, exc)
            self.store.record_film(
                film.slug,
                status="error",
                title=film.title,
                year=film.year,
                detail=str(exc),
            )
            summary.failed += 1
            return

        if tmdb_id is None:
            # Usually a TV series or an untracked entry; nothing Radarr can do.
            log.warning("%s: no TMDB movie id on Letterboxd, skipping", film)
            self.store.record_film(
                film.slug,
                status="no_tmdb_id",
                title=film.title,
                year=film.year,
                detail="no TMDB movie link on the Letterboxd film page",
            )
            summary.skipped_no_tmdb += 1
            return

        if self.config.dry_run:
            log.info("dry run: would add %s (TMDB %d) to Radarr", film, tmdb_id)
            self.store.record_film(
                film.slug,
                status="dry_run",
                title=film.title,
                year=film.year,
                tmdb_id=tmdb_id,
            )
            summary.added += 1
            return

        try:
            result = self.radarr.add_movie(
                tmdb_id,
                root_folder=self.config.root_folder,
                quality_profile_id=self._quality_profile_id,
                minimum_availability=self.config.minimum_availability,
                monitored=self.config.monitor,
                search_on_add=self.config.search_on_add,
                tag_ids=self._tag_ids or [],
            )
        except MovieAlreadyExists:
            log.info("%s: already in Radarr", film)
            self.store.record_film(
                film.slug,
                status="exists",
                title=film.title,
                year=film.year,
                tmdb_id=tmdb_id,
            )
            summary.already_in_radarr += 1
            return
        except RadarrError as exc:
            log.error("%s: Radarr rejected the add: %s", film, exc)
            self.store.record_film(
                film.slug,
                status="error",
                title=film.title,
                year=film.year,
                tmdb_id=tmdb_id,
                detail=str(exc),
            )
            summary.failed += 1
            return

        if result.already_existed:
            log.info("%s: already in Radarr", film)
            summary.already_in_radarr += 1
            status = "exists"
        else:
            log.info(
                "%s: added to Radarr%s",
                film,
                " and searching" if self.config.search_on_add else "",
            )
            summary.added += 1
            status = "added"

        self.store.record_film(
            film.slug,
            status=status,
            title=result.title or film.title,
            year=result.year or film.year,
            tmdb_id=tmdb_id,
            radarr_id=result.radarr_id,
        )
