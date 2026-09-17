"""A read-only preflight report, for confirming a deployment is wired up.

Everything here only reads: no Radarr writes, no state file, no tags created.
It exists so the first thing you run on a new install tells you which piece
is misconfigured, rather than making you infer it from a stack trace.
"""

from __future__ import annotations

from .config import Config
from .letterboxd import LetterboxdClient, LetterboxdError
from .radarr import RadarrAuthError, RadarrClient, RadarrError

OK = "  [ ok ]"
BAD = "  [FAIL]"
WARN = "  [warn]"


def run_check(
    config: Config, letterboxd: LetterboxdClient, radarr: RadarrClient
) -> int:
    """Print a connectivity and configuration report. Returns an exit code."""
    problems: list[str] = []

    print(f"\nRadarr at {config.radarr_url}")

    version = None
    try:
        version = radarr.system_status().get("version", "?")
        print(f"{OK} reachable, version {version}")
    except RadarrAuthError as exc:
        # Reached Radarr, so the URL and networking are fine -- only the key
        # is wrong. Saying "unreachable" here sends people to debug networking.
        print(f"{OK} reachable at {config.radarr_url}")
        print(f"{BAD} {exc}")
        print("       The URL is correct -- Radarr answered. Only the key is wrong.")
        print(
            "       Copy it from Settings -> General -> Security, or read it\n"
            "       straight from the container:\n"
            "         docker exec radarr sed -n "
            "'s|.*<ApiKey>\\(.*\\)</ApiKey>.*|\\1|p' /config/config.xml"
        )
        problems.append("api key")
    except RadarrError as exc:
        print(f"{BAD} cannot reach Radarr: {exc}")
        print(
            "       If Radarr runs in another Docker stack, a hostname like\n"
            "       'radarr' only resolves on a shared Docker network. Try the\n"
            "       host's LAN IP instead, e.g. http://192.168.1.10:7878"
        )
        problems.append("radarr unreachable")

    if version is not None:
        try:
            radarr.validate_root_folder(config.root_folder)
            print(f"{OK} root folder {config.root_folder}")
        except RadarrError as exc:
            print(f"{BAD} {exc}")
            print(
                "       This path is as Radarr sees it, not as the host sees it."
            )
            problems.append("root folder")

        try:
            profile_id = radarr.resolve_quality_profile(config.quality_profile)
            print(f"{OK} quality profile {config.quality_profile!r} (id {profile_id})")
        except RadarrError as exc:
            print(f"{BAD} {exc}")
            problems.append("quality profile")

        try:
            depth = radarr.queue_count()
            print(f"{OK} download queue readable ({depth} item(s) now)")
        except RadarrError as exc:
            print(f"{BAD} cannot read the download queue: {exc}")
            print("       Without this the one-at-a-time limit cannot be enforced.")
            problems.append("queue")

    print("\nLetterboxd watchlists")
    total_films = 0
    for username in config.letterboxd_users:
        try:
            watchlist = letterboxd.fetch_watchlist(username)
        except LetterboxdError as exc:
            print(f"{BAD} {username}: {exc}")
            print("       Is the watchlist public? Private ones return 404.")
            problems.append(f"watchlist {username}")
            continue

        total_films += len(watchlist.films)
        print(f"{OK} {username}: {len(watchlist.films)} films")
        if watchlist.films:
            newest = watchlist.films[0]
            print(f"       most recently added: {newest}")

    print("\nWhat will happen on the first real run")
    if config.seed_on_first_run:
        print(
            f"       {total_films} existing films recorded as a baseline, "
            "nothing requested."
        )
        print("       Only films added after that point go to Radarr.")
    else:
        print(f"{WARN} SEED_ON_FIRST_RUN=false: all {total_films} films will be")
        print("       requested. At the current limits this drains slowly.")

    if config.max_active_downloads > 0 and config.search_on_add:
        print(
            f"       At most {config.max_active_downloads} download(s) at a time."
        )
    elif config.max_adds_per_run > 0:
        print(f"       At most {config.max_adds_per_run} add(s) per pass.")
    else:
        print(f"{WARN} No download throttle: films are added as fast as found.")

    print()
    if problems:
        print(f"{len(problems)} problem(s) to fix: {', '.join(problems)}\n")
        return 1

    print("All checks passed. Nothing was written to Radarr or to the state file.\n")
    return 0
