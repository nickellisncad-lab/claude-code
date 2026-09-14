# letterboxd-radarr-sync

Watches the public Letterboxd watchlists of one or more users and sends anything
newly added to **Radarr**, which downloads it into your Plex movie library.

```
Letterboxd watchlist  ──poll──▶  this service  ──/api/v3/movie──▶  Radarr  ──▶  Plex library
```

Currently configured for `nickelliis` and `karlasalgadox`.

## How it works

1. Every `POLL_INTERVAL` seconds it scrapes each user's watchlist.
2. It diffs that against a SQLite record of what it has seen before.
3. For each genuinely new film it reads the Letterboxd film page to get the
   **TMDB id** — the only identifier Radarr and Letterboxd reliably share.
4. It adds the movie to Radarr with your root folder, quality profile, and tags,
   and optionally triggers a search straight away.

A film on **both** watchlists is requested once. A film already in Radarr is
recognised and left alone.

## Setup

```bash
git clone <this repo>
cd letterboxd-radarr-sync
cp .env.example .env
$EDITOR .env          # at minimum: RADARR_URL, RADARR_API_KEY, RADARR_ROOT_FOLDER
docker compose up -d
docker compose logs -f
```

`RADARR_ROOT_FOLDER` and `RADARR_QUALITY_PROFILE` must match Radarr exactly. If
they don't, startup fails immediately and the log lists the valid values — it
won't scrape anything first.

### Check it before letting it write

```bash
docker compose run --rm letterboxd-radarr-sync --once --dry-run
```

This scrapes both watchlists and logs exactly what it *would* add, without
touching Radarr. Dry-run results are not treated as done, so the first real run
still picks those films up.

## The first run

By default (`SEED_ON_FIRST_RUN=true`) the first pass records both watchlists as
a **baseline and requests nothing**. Only films added afterwards get sent to
Radarr.

That default exists because the two configured watchlists currently hold about
190 films between them, and importing all of them on startup would saturate your
indexers and download client in one go. If you *do* want the existing backlog
imported, set `SEED_ON_FIRST_RUN=false` before the first run — it has no effect
once a user has been seeded.

## Configuration

Every setting is an environment variable; see `.env.example` for the annotated
list. The ones worth knowing about:

| Variable | Default | Notes |
|---|---|---|
| `LETTERBOXD_USERS` | *required* | Comma-separated. Watchlists must be public. |
| `RADARR_URL` | *required* | Base URL, no `/api` suffix. |
| `RADARR_API_KEY` | *required* | Radarr → Settings → General → Security. |
| `RADARR_ROOT_FOLDER` | *required* | Must match a configured Radarr root folder. |
| `RADARR_QUALITY_PROFILE` | *required* | Profile name or numeric id. |
| `RADARR_SEARCH_ON_ADD` | `true` | `false` adds without searching. |
| `RADARR_TAGS` | — | Created in Radarr if absent. Handy for filtering later. |
| `POLL_INTERVAL` | `3600` | Seconds. Minimum 60. |
| `SEED_ON_FIRST_RUN` | `true` | See above. |
| `DRY_RUN` | `false` | Log intended adds, write nothing. |
| `REQUEST_DELAY` | `1.0` | Seconds between Letterboxd requests. |

## State

`/data/state.db` (SQLite) holds which films each user has watchlisted and what
happened to each one. **Keep the `./data` volume.** Delete it and the next run
re-seeds from scratch — with `SEED_ON_FIRST_RUN=true` that means new additions
made while it was gone are silently missed; with `false` it means re-requesting
everything.

Film outcomes are recorded as:

| Status | Meaning |
|---|---|
| `added` | Sent to Radarr. |
| `exists` | Radarr already had it. |
| `no_tmdb_id` | No TMDB *movie* link on Letterboxd — usually a TV series. Skipped permanently. |
| `error` | Retried on the next pass. |
| `dry_run` | Would have been added; retried for real on the next non-dry run. |

Inspect it directly:

```bash
sqlite3 data/state.db "SELECT title, year, status FROM films ORDER BY updated_at DESC LIMIT 20;"
```

## Running without Docker

```bash
pip install -r requirements.txt
export $(grep -v '^#' .env | xargs)
PYTHONPATH=src python -m lbxd_sync --once
```

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest
```

No test touches the network — Letterboxd pages come from HTML fixtures and
Radarr is stubbed with `responses`.

## Caveats worth knowing

**This scrapes HTML.** Letterboxd's API is still invite-only beta, and
watchlists have no RSS feed (unlike diary entries), so there is no supported
interface to use. Letterboxd has changed this markup before — the grid moved
from `li.poster-container`/`data-film-slug` to `li.griditem`/`data-item-slug` —
so the parser tries both and, if a watchlist page yields zero films while the
page header says the user has some, it **raises instead of reporting an empty
watchlist**. A silent "0 new films" forever is the failure this guards against;
if you see `WatchlistParseError` in the logs, the selectors in
`src/lbxd_sync/letterboxd.py` need updating.

**Watchlists must be public.** A private or friends-only watchlist returns 404
to an anonymous fetch and is logged as an error for that user; the other user
still syncs normally.

**TV series are skipped.** Radarr only handles movies. Series on a watchlist are
marked `no_tmdb_id` and ignored. Pointing them at Sonarr would be a separate
piece of work.

**Be polite.** `REQUEST_DELAY` throttles requests and the default hourly poll is
a handful of page loads. Please don't set the interval to seconds.

**Removals are not mirrored.** Taking a film off a Letterboxd watchlist does not
remove it from Radarr; that stays a deliberate manual decision.
