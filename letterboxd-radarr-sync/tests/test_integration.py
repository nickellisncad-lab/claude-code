"""Drives main() end to end with both HTTP services faked."""

import json

import responses

from lbxd_sync.__main__ import main

RADARR = "http://radarr:7878/api/v3"
LB = "https://letterboxd.com"


def _radarr_preflight():
    responses.add(responses.GET, f"{RADARR}/system/status", json={"version": "5.2.6"})
    responses.add(responses.GET, f"{RADARR}/rootfolder", json=[{"id": 1, "path": "/movies"}])
    responses.add(
        responses.GET, f"{RADARR}/qualityprofile", json=[{"id": 4, "name": "HD-1080p"}]
    )
    responses.add(responses.GET, f"{RADARR}/tag", json=[{"id": 2, "label": "letterboxd"}])
    responses.add(
        responses.GET, f"{RADARR}/queue", json={"totalRecords": 0, "records": []}
    )


def _env(monkeypatch, tmp_path, **overrides):
    settings = {
        "LETTERBOXD_USERS": "nickelliis",
        "RADARR_URL": "http://radarr:7878",
        "RADARR_API_KEY": "key",
        "RADARR_ROOT_FOLDER": "/movies",
        "RADARR_QUALITY_PROFILE": "HD-1080p",
        "RADARR_TAGS": "letterboxd",
        "STATE_PATH": str(tmp_path / "state.db"),
        "REQUEST_DELAY": "0",
        "POLL_INTERVAL": "60",
        "LOG_LEVEL": "DEBUG",
    }
    settings.update(overrides)
    for key, value in settings.items():
        monkeypatch.setenv(key, value)


@responses.activate
def test_seed_then_add_end_to_end(monkeypatch, tmp_path, fixture):
    _env(monkeypatch, tmp_path)
    _radarr_preflight()
    responses.add(
        responses.GET, f"{LB}/nickelliis/watchlist/", body=fixture("watchlist_page1.html")
    )
    responses.add(
        responses.GET,
        f"{LB}/nickelliis/watchlist/page/2/",
        body=fixture("watchlist_page2.html"),
    )

    # First pass seeds the baseline and must not POST anything to Radarr.
    assert main(["--once"]) == 0
    assert not [c for c in responses.calls if c.request.method == "POST"]

    # Second pass: the user adds Parasite... which was already in the baseline,
    # so still nothing. Simulate a genuinely new film instead.
    responses.reset()
    _radarr_preflight()
    responses.add(
        responses.GET,
        f"{LB}/nickelliis/watchlist/",
        body=fixture("watchlist_page1.html").replace(
            "the-zone-of-interest", "la-chimera"
        ),
    )
    responses.add(
        responses.GET, f"{LB}/nickelliis/watchlist/page/2/", body=fixture("watchlist_page2.html")
    )
    responses.add(responses.GET, f"{LB}/film/la-chimera/", body=fixture("film_page.html"))
    responses.add(responses.GET, f"{RADARR}/movie", json=[])
    responses.add(
        responses.GET,
        f"{RADARR}/movie/lookup/tmdb",
        json={"title": "Parasite", "year": 2019, "titleSlug": "parasite-496243", "tmdbId": 496243},
    )
    responses.add(
        responses.POST, f"{RADARR}/movie", json={"id": 55, "title": "Parasite", "year": 2019}, status=201
    )

    assert main(["--once"]) == 0

    posts = [c for c in responses.calls if c.request.method == "POST"]
    assert len(posts) == 1
    payload = json.loads(posts[0].request.body)
    assert payload["tmdbId"] == 496243
    assert payload["rootFolderPath"] == "/movies"
    assert payload["qualityProfileId"] == 4
    assert payload["tags"] == [2]


@responses.activate
def test_preflight_failure_exits_nonzero_before_scraping(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path, RADARR_QUALITY_PROFILE="Nonexistent")
    responses.add(responses.GET, f"{RADARR}/system/status", json={"version": "5.2.6"})
    responses.add(responses.GET, f"{RADARR}/rootfolder", json=[{"id": 1, "path": "/movies"}])
    responses.add(responses.GET, f"{RADARR}/qualityprofile", json=[{"id": 4, "name": "HD-1080p"}])

    assert main(["--once"]) == 1
    # Nothing on letterboxd.com should have been touched.
    assert not [c for c in responses.calls if "letterboxd" in c.request.url]


@responses.activate
def test_dry_run_flag_writes_nothing(monkeypatch, tmp_path, fixture):
    _env(monkeypatch, tmp_path, SEED_ON_FIRST_RUN="false")
    _radarr_preflight()
    responses.add(
        responses.GET, f"{LB}/nickelliis/watchlist/", body=fixture("watchlist_page1.html")
    )
    responses.add(
        responses.GET, f"{LB}/nickelliis/watchlist/page/2/", body=fixture("watchlist_page2.html")
    )
    for slug in ("parasite-2019", "the-zone-of-interest", "perfect-days-2023"):
        responses.add(responses.GET, f"{LB}/film/{slug}/", body=fixture("film_page.html"))

    assert main(["--once", "--dry-run"]) == 0
    assert not [c for c in responses.calls if c.request.method == "POST"]
