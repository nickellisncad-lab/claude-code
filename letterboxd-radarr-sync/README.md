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

Run this on the machine that runs Radarr, or anywhere that can reach it.

```bash
git clone https://github.com/nickellisncad-lab/claude-code.git
cd claude-code/letterboxd-radarr-sync
./setup.sh
```

`setup.sh` asks for the four Radarr values below, writes `.env` with correct
permissions, and runs the read-only connectivity check. It never overwrites an
existing `.env` without asking and writes nothing to Radarr.

To do it by hand instead:

```bash
cp .env.example .env
$EDITOR .env
chmod 600 .env          # it holds your Radarr API key
```

You need four values from Radarr:

| Setting | Where to find it |
|---|---|
| `RADARR_API_KEY` | Radarr → Settings → General → Security → API Key |
| `RADARR_ROOT_FOLDER` | Radarr → Settings → Media Management → Root Folders. Copy it **exactly** — this is the path *as Radarr sees it*, which in Docker is usually `/movies`, not the host path. |
| `RADARR_QUALITY_PROFILE` | Radarr → Settings → Profiles. Name, case-insensitive. |
| `RADARR_URL` | See below — this is the one people get wrong. |

### Getting `RADARR_URL` right

| Your setup | Use |
|---|---|
| Radarr in the **same** compose stack | `http://radarr:7878` and put this service on that network |
| Radarr in a **different** Docker stack | `http://<host LAN IP>:7878`, e.g. `http://192.168.1.10:7878` |
| Radarr directly on the host | `http://<host LAN IP>:7878` |

A bare hostname like `radarr` only resolves if both containers share a Docker
network. `localhost` never works from inside the container — it means the
container itself.

### Verify before letting it write anything

```bash
docker compose run --rm letterboxd-radarr-sync --check
```

This is read-only: it confirms Radarr is reachable, the API key works, the root
folder and quality profile exist, the download queue is readable, and both
watchlists are public — then tells you what the first real run will do. It
writes nothing to Radarr and does not create the state file. Fix anything it
flags before continuing.

Then, to see the actual decisions without writing to Radarr:

```bash
docker compose run --rm letterboxd-radarr-sync --once --dry-run
```

### Start it

```bash
docker compose up -d
docker compose logs -f
```

`restart: unless-stopped` means it comes back after a reboot or a Docker
restart. That is the whole of "keeping it running" — there is no cron to set up,
no systemd unit to write; the container sleeps between passes on its own.

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
| `MAX_ACTIVE_DOWNLOADS` | `1` | Hold off adding while Radarr's queue is this deep. `0` = no limit. |
| `MAX_ADDS_PER_RUN` | `1` | Cap adds per pass regardless of queue. `0` = no limit. |
| `RADARR_TAGS` | — | Created in Radarr if absent. Handy for filtering later. |
| `POLL_INTERVAL` | `3600` | Seconds. Minimum 60. |
| `SEED_ON_FIRST_RUN` | `true` | See above. |
| `DRY_RUN` | `false` | Log intended adds, write nothing. |
| `REQUEST_DELAY` | `1.0` | Seconds between Letterboxd requests. |

## One movie at a time

By default this adds **at most one movie per pass, and only when Radarr's
download queue is empty**. Anything else found in the same pass is recorded as
`deferred` and picked up on a later pass — deferred films are tried *before*
newly discovered ones, so a backlog item can't be starved by a steady trickle
of new watchlist additions.

If the Radarr queue can't be read, the pass **defers rather than adds**. Failing
closed is the right direction when the question is "is something already
downloading?".

Two knobs, and they do different jobs:

- `MAX_ACTIVE_DOWNLOADS=1` is the real one-at-a-time gate. It reads Radarr's
  queue before adding. It only means anything when `RADARR_SEARCH_ON_ADD=true`,
  since an add that starts no search downloads nothing.
- `MAX_ADDS_PER_RUN=1` caps adds per pass regardless of the queue, which still
  applies when searching is off.

With the shipped defaults these two produce **identical** behaviour: the queue
gate already allows only one add per pass, so `MAX_ADDS_PER_RUN=1` and `0` do
the same thing here. Leave it at `1` — it costs nothing and still protects you
if the queue reading is ever wrong or you turn searching off.

### What this does *not* control

This throttles what **we** hand to Radarr. It cannot stop Radarr from starting a
download on its own: a monitored film that wasn't available when added gets
picked up later by Radarr's own RSS sync, outside our control. If you need a
hard ceiling on concurrent downloads, set it in the **download client**
(qBittorrent's "Maximum active downloads", SABnzbd's queue settings) — that is
the only place it's genuinely enforced.

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
