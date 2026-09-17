"""Entry point: run one sync pass, or poll on an interval."""

from __future__ import annotations

import argparse
import dataclasses
import logging
import signal
import sys
import threading

import requests

from . import __version__
from .check import run_check
from .config import Config, ConfigError
from .letterboxd import LetterboxdClient
from .radarr import RadarrClient, RadarrError
from .state import StateError, Store
from .sync import Syncer

log = logging.getLogger("lbxd_sync")

_shutdown = threading.Event()


def _handle_signal(signum, _frame) -> None:
    log.info("received %s, shutting down after this pass", signal.Signals(signum).name)
    _shutdown.set()


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
        stream=sys.stdout,
    )
    # These are chatty at DEBUG and leak request URLs we do not need.
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="lbxd-sync",
        description="Mirror Letterboxd watchlist additions into Radarr.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="run a single sync pass and exit instead of polling",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify Radarr and both watchlists are reachable, then exit; "
        "writes nothing to Radarr or to the state file",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would be requested without writing to Radarr",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    try:
        config = Config.from_env()
    except ConfigError as exc:
        # Logging is not configured yet; this has to reach the user regardless.
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2

    if args.dry_run:
        config = dataclasses.replace(config, dry_run=True)

    _configure_logging(config.log_level)
    if not args.check:
        log.info(
            "letterboxd-radarr-sync %s starting (users: %s, interval: %ds%s)",
            __version__,
            ", ".join(config.letterboxd_users),
            config.poll_interval,
            ", DRY RUN" if config.dry_run else "",
        )

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    session = requests.Session()
    letterboxd = LetterboxdClient(
        user_agent=config.user_agent,
        delay=config.request_delay,
        timeout=config.request_timeout,
        max_retries=config.max_retries,
    )
    radarr = RadarrClient(
        config.radarr_url,
        config.radarr_api_key,
        session=session,
        timeout=config.request_timeout,
        max_retries=config.max_retries,
    )

    if args.check:
        # Deliberately never opens the real state file.
        return run_check(config, letterboxd, radarr)

    try:
        store = Store(config.state_path)
    except StateError as exc:
        log.error("%s", exc)
        return 1

    with store:
        syncer = Syncer(config, letterboxd, radarr, store)

        try:
            syncer.preflight()
        except RadarrError as exc:
            log.error("Radarr preflight failed: %s", exc)
            return 1

        first_pass = True
        while first_pass or not _shutdown.is_set():
            first_pass = False
            try:
                summary = syncer.run_once()
            except Exception:  # keep the daemon alive across a bad pass
                log.exception("sync pass failed")
                if args.once:
                    return 1
            else:
                log.info("sync pass complete: %s", summary.describe())
                if args.once:
                    return 0 if summary.ok else 1

            if _shutdown.is_set():
                break
            # Event.wait lets SIGTERM cut the sleep short.
            _shutdown.wait(config.poll_interval)

    log.info("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
