# claude-code

The code lives in `letterboxd-radarr-sync/`, not at the repository root. Run
everything below from that directory.

## Commands

```bash
pip install -r requirements-dev.txt   # test deps; fresh containers lack pytest
python -m pytest -q                   # full suite
python -m pytest tests/test_sync.py   # single file
```

`pyproject.toml` sets `pythonpath = ["src"]`, so tests import `lbxd_sync`
without an editable install.

There is no configured linter or formatter. CI runs pytest on Python 3.11,
3.12 and 3.13, plus a Docker build that checks the entrypoint runs and that a
missing config exits 2 rather than crashing.

## Layout

`src/lbxd_sync/` — `letterboxd.py` scrapes the watchlist, `radarr.py` talks to
the Radarr API, `sync.py` drives one pass, `state.py` persists what has already
been added, `check.py` is the read-only `--check` mode, `config.py` reads env
vars. Tests mock HTTP with `responses`; HTML fixtures live in
`tests/fixtures/`.

## Conventions

Python 3.11+. Requests are throttled to one download at a time on purpose —
keep it that way unless asked. Config comes from environment variables only
(see `.env.example`); `LETTERBOXD_USERS` is required and its absence must exit
2 with a clear message, which CI asserts.
