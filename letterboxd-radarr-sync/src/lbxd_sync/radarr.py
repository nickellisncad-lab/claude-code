"""A thin client for the Radarr v3 API."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import requests

log = logging.getLogger(__name__)


class RadarrError(Exception):
    """A Radarr request failed."""


class MovieAlreadyExists(RadarrError):
    """Radarr rejected the add because the movie is already in the library."""


@dataclass(frozen=True)
class AddResult:
    """Outcome of adding one movie."""

    tmdb_id: int
    title: str
    year: int | None
    radarr_id: int | None
    already_existed: bool


class RadarrClient:
    """Talks to Radarr over its v3 REST API."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        session: requests.Session | None = None,
        timeout: float = 30.0,
        max_retries: int = 3,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        # Radarr accepts the key as a header, which keeps it out of any
        # request logging that records URLs.
        self.session.headers.update({"X-Api-Key": api_key})
        self.timeout = timeout
        self.max_retries = max_retries

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        url = f"{self.base_url}/api/v3/{path.lstrip('/')}"
        last_error: Exception | None = None

        for attempt in range(1, self.max_retries + 1):
            try:
                response = self.session.request(
                    method, url, timeout=self.timeout, **kwargs
                )
            except requests.RequestException as exc:
                last_error = exc
                log.warning("%s %s failed (attempt %d): %s", method, url, attempt, exc)
            else:
                if response.status_code >= 500:
                    last_error = RadarrError(
                        f"{method} {path} returned HTTP {response.status_code}"
                    )
                    log.warning(
                        "%s %s returned %d (attempt %d)",
                        method,
                        url,
                        response.status_code,
                        attempt,
                    )
                else:
                    return response
            if attempt < self.max_retries:
                time.sleep(2.0 ** attempt)

        raise RadarrError(
            f"{method} {path} failed after {self.max_retries} attempts: {last_error}"
        )

    def _json(self, method: str, path: str, **kwargs):
        response = self._request(method, path, **kwargs)
        if response.status_code == 401:
            raise RadarrError("Radarr rejected the API key (HTTP 401)")
        if response.status_code >= 400:
            raise RadarrError(
                f"{method} {path} returned HTTP {response.status_code}: "
                f"{response.text[:300]}"
            )
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise RadarrError(f"{method} {path} returned invalid JSON") from exc

    # -- reads ------------------------------------------------------------

    def system_status(self) -> dict:
        return self._json("GET", "system/status")

    def quality_profiles(self) -> list[dict]:
        return self._json("GET", "qualityprofile") or []

    def root_folders(self) -> list[dict]:
        return self._json("GET", "rootfolder") or []

    def tags(self) -> list[dict]:
        return self._json("GET", "tag") or []

    def resolve_quality_profile(self, wanted: str) -> int:
        """Resolve a quality profile by id or (case-insensitive) name."""
        profiles = self.quality_profiles()
        if wanted.isdigit():
            target = int(wanted)
            for profile in profiles:
                if profile["id"] == target:
                    return target
            raise RadarrError(f"no Radarr quality profile with id {target}")

        for profile in profiles:
            if profile["name"].casefold() == wanted.casefold():
                return profile["id"]

        available = ", ".join(sorted(p["name"] for p in profiles)) or "none"
        raise RadarrError(
            f"no Radarr quality profile named {wanted!r} (available: {available})"
        )

    def validate_root_folder(self, path: str) -> None:
        folders = self.root_folders()
        known = {folder["path"].rstrip("/") for folder in folders}
        if path.rstrip("/") not in known:
            available = ", ".join(sorted(known)) or "none"
            raise RadarrError(
                f"{path!r} is not a Radarr root folder (available: {available})"
            )

    def ensure_tags(self, labels: list[str]) -> list[int]:
        """Return tag ids for ``labels``, creating any that do not exist."""
        if not labels:
            return []
        existing = {tag["label"].casefold(): tag["id"] for tag in self.tags()}
        ids = []
        for label in labels:
            tag_id = existing.get(label.casefold())
            if tag_id is None:
                created = self._json("POST", "tag", json={"label": label})
                tag_id = created["id"]
                log.info("created Radarr tag %r (id %d)", label, tag_id)
            ids.append(tag_id)
        return ids

    def queue_count(self) -> int:
        """How many items Radarr currently has in its download queue."""
        result = self._json("GET", "queue", params={"page": 1, "pageSize": 1})
        if isinstance(result, dict):
            return int(result.get("totalRecords", 0))
        if isinstance(result, list):
            return len(result)
        return 0

    def lookup_by_tmdb(self, tmdb_id: int) -> dict | None:
        """Look a movie up in Radarr's metadata by TMDB id."""
        result = self._json(
            "GET", "movie/lookup/tmdb", params={"tmdbId": tmdb_id}
        )
        if isinstance(result, list):
            return result[0] if result else None
        return result or None

    def get_movie_by_tmdb(self, tmdb_id: int) -> dict | None:
        """Return the library entry for ``tmdb_id`` if Radarr already has it."""
        result = self._json("GET", "movie", params={"tmdbId": tmdb_id})
        if isinstance(result, list):
            return result[0] if result else None
        return result or None

    # -- writes -----------------------------------------------------------

    def add_movie(
        self,
        tmdb_id: int,
        *,
        root_folder: str,
        quality_profile_id: int,
        minimum_availability: str = "released",
        monitored: bool = True,
        search_on_add: bool = True,
        tag_ids: list[int] | None = None,
    ) -> AddResult:
        """Add a movie to Radarr, optionally kicking off a search."""
        existing = self.get_movie_by_tmdb(tmdb_id)
        if existing:
            return AddResult(
                tmdb_id=tmdb_id,
                title=existing.get("title", ""),
                year=existing.get("year"),
                radarr_id=existing.get("id"),
                already_existed=True,
            )

        lookup = self.lookup_by_tmdb(tmdb_id)
        if not lookup:
            raise RadarrError(f"Radarr could not find TMDB id {tmdb_id}")

        payload = {
            "tmdbId": tmdb_id,
            "title": lookup.get("title"),
            "year": lookup.get("year"),
            "titleSlug": lookup.get("titleSlug"),
            "images": lookup.get("images", []),
            "rootFolderPath": root_folder,
            "qualityProfileId": quality_profile_id,
            "minimumAvailability": minimum_availability,
            "monitored": monitored,
            "tags": tag_ids or [],
            "addOptions": {"searchForMovie": search_on_add},
        }

        response = self._request("POST", "movie", json=payload)

        if response.status_code in (200, 201):
            created = response.json()
            return AddResult(
                tmdb_id=tmdb_id,
                title=created.get("title", payload["title"] or ""),
                year=created.get("year", payload["year"]),
                radarr_id=created.get("id"),
                already_existed=False,
            )

        # Radarr answers a duplicate add with a 400 carrying MovieExistsValidator.
        if response.status_code == 400 and "MovieExists" in response.text:
            raise MovieAlreadyExists(f"TMDB id {tmdb_id} is already in Radarr")

        raise RadarrError(
            f"adding TMDB id {tmdb_id} returned HTTP {response.status_code}: "
            f"{response.text[:300]}"
        )
