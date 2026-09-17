"""Reading public Letterboxd watchlists.

Letterboxd has no public API for watchlists (their API is still invite-only
beta), so this scrapes the public HTML. The markup has changed before -- the
grid used to be ``li.poster-container`` with ``data-film-slug`` and is now
``li.griditem`` with ``data-item-slug`` -- so the parser tries the known
variants and, crucially, refuses to report an empty watchlist when the page
header says otherwise. A silent "0 new films" forever is the failure mode
worth engineering against.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

BASE_URL = "https://letterboxd.com"

# "nickelliis wants to see 44 films" on the watchlist page header.
_COUNT_RE = re.compile(r"wants to see\s+([\d,]+)\s+film", re.IGNORECASE)
_PAGE_RE = re.compile(r"/page/(\d+)/?$")
_YEAR_RE = re.compile(r"\((\d{4})\)\s*$")
_TRAILING_YEAR_RE = re.compile(r"\s*\((\d{4})\)\s*$")
_TMDB_MOVIE_RE = re.compile(r"themoviedb\.org/movie/(\d+)")
_FILM_HREF_RE = re.compile(r"^/film/([^/]+)/")

# Selectors for one watchlist entry, current markup first.
_ITEM_SELECTORS = ("li.griditem", "li.poster-container")
# Attributes carrying the film slug, current markup first.
_SLUG_ATTRS = ("data-item-slug", "data-film-slug")
_NAME_ATTRS = ("data-item-name", "data-film-name")
_DISPLAY_ATTRS = ("data-item-full-display-name", "data-film-full-display-name")

# A watchlist that needs more pages than this is almost certainly a bug.
MAX_PAGES = 200


class LetterboxdError(Exception):
    """Base class for Letterboxd failures."""


class WatchlistUnavailable(LetterboxdError):
    """The watchlist is missing, private, or the user does not exist."""


class WatchlistParseError(LetterboxdError):
    """The page loaded but no films could be parsed out of it.

    Almost always means Letterboxd changed their markup and the selectors
    below need updating.
    """


@dataclass(frozen=True)
class Film:
    """One film on a watchlist."""

    slug: str
    title: str
    year: int | None = None

    @property
    def url(self) -> str:
        return f"{BASE_URL}/film/{self.slug}/"

    def __str__(self) -> str:
        return f"{self.title} ({self.year})" if self.year else self.title


@dataclass(frozen=True)
class Watchlist:
    """A user's watchlist as scraped in one pass."""

    username: str
    films: list[Film]
    stated_total: int | None
    """The count Letterboxd printed in the page header, when we could read it."""


def _attr_text(value) -> str:
    """Normalise an attribute value to text.

    BeautifulSoup hands back a list for multi-valued attributes; calling
    .strip() on one raises and would take down the whole sync pass.
    """
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return " ".join(str(part) for part in value).strip()
    return str(value).strip()


def _first_attr(element, attrs: tuple[str, ...]) -> str | None:
    """Return the first of ``attrs`` present on ``element`` or its descendants."""
    for attr in attrs:
        own = _attr_text(element.get(attr))
        if own:
            return own
        found = element.select_one(f"[{attr}]")
        if found is not None:
            nested = _attr_text(found.get(attr))
            if nested:
                return nested
    return None


def _strip_trailing_year(title: str, year: int | None) -> str:
    """Drop a trailing "(1988)" from a title that already carries its year.

    Letterboxd's live markup puts the year in the title attribute as well as
    the display name, so without this a film renders as
    "Dirty Rotten Scoundrels (1988) (1988)".
    """
    match = _TRAILING_YEAR_RE.search(title)
    if match and (year is None or int(match.group(1)) == year):
        stripped = title[: match.start()].strip()
        if stripped:
            return stripped
    return title


def _parse_year(display_name: str | None) -> int | None:
    if not display_name:
        return None
    match = _YEAR_RE.search(display_name)
    return int(match.group(1)) if match else None


def parse_watchlist_page(html: str) -> tuple[list[Film], int | None, int | None]:
    """Parse one watchlist page.

    Returns ``(films, last_page, stated_total)``. ``last_page`` is the highest
    page number in the paginator, or None when the list fits on one page.
    """
    soup = BeautifulSoup(html, "lxml")

    stated_total = None
    header = soup.find(string=_COUNT_RE)
    if header:
        match = _COUNT_RE.search(str(header))
        if match:
            stated_total = int(match.group(1).replace(",", ""))

    items = []
    for selector in _ITEM_SELECTORS:
        items = soup.select(selector)
        if items:
            break

    films: list[Film] = []
    seen: set[str] = set()
    for item in items:
        slug = _first_attr(item, _SLUG_ATTRS)
        if not slug:
            # Fall back to whatever link the entry points at.
            target = _first_attr(item, ("data-target-link",))
            link = item.select_one('a[href^="/film/"]')
            href = target or (link["href"] if link else None)
            if href:
                match = _FILM_HREF_RE.match(href)
                if match:
                    slug = match.group(1)
        if not slug or slug in seen:
            continue
        seen.add(slug)

        display_name = _first_attr(item, _DISPLAY_ATTRS)
        raw_title = _first_attr(item, _NAME_ATTRS) or display_name or slug
        year = _parse_year(display_name) or _parse_year(raw_title)
        films.append(
            Film(slug=slug, title=_strip_trailing_year(raw_title, year), year=year)
        )

    last_page = None
    for anchor in soup.select(".pagination a[href]"):
        match = _PAGE_RE.search(anchor["href"])
        if match:
            page = int(match.group(1))
            last_page = page if last_page is None else max(last_page, page)

    return films, last_page, stated_total


def extract_tmdb_id(html: str) -> int | None:
    """Pull the TMDB movie id out of a Letterboxd film page.

    Returns None for entries with no TMDB movie link -- TV series link to
    ``/tv/`` instead, and a few obscure entries have no link at all.
    """
    soup = BeautifulSoup(html, "lxml")

    # Prefer the film's own details footer, so a TMDB link anywhere else on
    # the page cannot be mistaken for this film's id.
    for container in soup.select("p.text-link, .text-footer"):
        for anchor in container.select("a[href]"):
            match = _TMDB_MOVIE_RE.search(anchor["href"])
            if match:
                return int(match.group(1))

    match = _TMDB_MOVIE_RE.search(html)
    return int(match.group(1)) if match else None


class LetterboxdClient:
    """Fetches watchlists and film metadata, politely."""

    def __init__(
        self,
        *,
        session: requests.Session | None = None,
        user_agent: str,
        delay: float = 1.0,
        timeout: float = 30.0,
        max_retries: int = 3,
    ) -> None:
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": user_agent})
        self.delay = delay
        self.timeout = timeout
        self.max_retries = max_retries
        self._last_request = 0.0

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if elapsed < self.delay:
            time.sleep(self.delay - elapsed)
        self._last_request = time.monotonic()

    def _get(self, url: str) -> requests.Response:
        """GET with a fixed retry budget, honouring Retry-After on 429."""
        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            self._throttle()
            try:
                response = self.session.get(url, timeout=self.timeout)
            except requests.RequestException as exc:
                last_error = exc
                log.warning("GET %s failed (attempt %d): %s", url, attempt, exc)
            else:
                if response.status_code == 404:
                    raise WatchlistUnavailable(f"{url} returned 404")
                if response.status_code == 429 or response.status_code >= 500:
                    retry_after = response.headers.get("Retry-After")
                    wait = float(retry_after) if retry_after else 2.0 ** attempt
                    last_error = LetterboxdError(
                        f"{url} returned HTTP {response.status_code}"
                    )
                    log.warning(
                        "GET %s returned %d, retrying in %.1fs (attempt %d)",
                        url,
                        response.status_code,
                        wait,
                        attempt,
                    )
                    if attempt < self.max_retries:
                        time.sleep(wait)
                    continue
                if response.status_code >= 400:
                    raise LetterboxdError(
                        f"{url} returned HTTP {response.status_code}"
                    )
                return response
            if attempt < self.max_retries:
                time.sleep(2.0 ** attempt)
        raise LetterboxdError(f"GET {url} failed after {self.max_retries} attempts: {last_error}")

    def fetch_watchlist(self, username: str) -> Watchlist:
        """Scrape every page of ``username``'s public watchlist."""
        films: list[Film] = []
        seen: set[str] = set()
        stated_total: int | None = None
        page = 1
        last_page: int | None = None

        while page <= MAX_PAGES:
            url = f"{BASE_URL}/{username}/watchlist/"
            if page > 1:
                url += f"page/{page}/"

            response = self._get(url)
            page_films, page_last, page_total = parse_watchlist_page(response.text)

            if page == 1:
                stated_total = page_total
                last_page = page_last
                # An empty first page with a non-zero header count means our
                # selectors no longer match the markup. Fail loudly rather
                # than quietly deciding nobody has watchlisted anything.
                if not page_films and (page_total is None or page_total > 0):
                    raise WatchlistParseError(
                        f"parsed 0 films from {url} but the page reports "
                        f"{page_total if page_total is not None else 'an unknown number of'} "
                        "films -- Letterboxd markup has probably changed"
                    )

            new_on_page = [film for film in page_films if film.slug not in seen]
            for film in new_on_page:
                seen.add(film.slug)
                films.append(film)

            # Nothing new: either a genuinely empty page, or a page number past
            # the end that echoed content we already have. Either way, stop.
            if not new_on_page:
                break

            if last_page is not None:
                if page >= last_page:
                    break
            elif stated_total is None or len(films) >= stated_total:
                # No paginator on the page. Trust the header count if we have
                # one, so a paginator markup change does not truncate the list.
                break

            page += 1

        if stated_total is not None and len(films) < stated_total:
            # Not fatal -- the header count can include entries the grid
            # filters out, and the list can change between page fetches -- but
            # a large shortfall usually means pagination broke, and a film we
            # never see is a film that never syncs.
            log.warning(
                "%s: scraped %d films but the page header said %d; "
                "some watchlist entries may have been missed",
                username,
                len(films),
                stated_total,
            )
        elif stated_total is not None and len(films) > stated_total:
            log.info(
                "%s: scraped %d films, page header said %d",
                username,
                len(films),
                stated_total,
            )

        return Watchlist(username=username, films=films, stated_total=stated_total)

    def fetch_tmdb_id(self, slug: str) -> int | None:
        """Return the TMDB movie id for a film slug, or None if it has none."""
        response = self._get(f"{BASE_URL}/film/{slug}/")
        return extract_tmdb_id(response.text)
