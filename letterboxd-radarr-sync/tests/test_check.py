"""The --check report must be read-only and must name the broken piece."""

import os

import responses

from lbxd_sync.__main__ import main

RADARR = "http://radarr:7878/api/v3"
LB = "https://letterboxd.com"


def _env(monkeypatch, tmp_path, **overrides):
    settings = {
        "LETTERBOXD_USERS": "nickelliis",
        "RADARR_URL": "http://radarr:7878",
        "RADARR_API_KEY": "key",
        "RADARR_ROOT_FOLDER": "/movies",
        "RADARR_QUALITY_PROFILE": "HD-1080p",
        "STATE_PATH": str(tmp_path / "state.db"),
        "REQUEST_DELAY": "0",
        "MAX_RETRIES": "1",
    }
    settings.update(overrides)
    for key, value in settings.items():
        monkeypatch.setenv(key, value)
    return tmp_path / "state.db"


def _healthy_radarr():
    responses.add(responses.GET, f"{RADARR}/system/status", json={"version": "5.2.6"})
    responses.add(responses.GET, f"{RADARR}/rootfolder", json=[{"id": 1, "path": "/movies"}])
    responses.add(
        responses.GET, f"{RADARR}/qualityprofile", json=[{"id": 4, "name": "HD-1080p"}]
    )
    responses.add(responses.GET, f"{RADARR}/queue", json={"totalRecords": 0})


def _watchlist(fixture):
    responses.add(
        responses.GET, f"{LB}/nickelliis/watchlist/", body=fixture("watchlist_page1.html")
    )
    responses.add(
        responses.GET, f"{LB}/nickelliis/watchlist/page/2/", body=fixture("watchlist_page2.html")
    )


@responses.activate
def test_check_passes_and_touches_nothing(monkeypatch, tmp_path, fixture, capsys):
    state = _env(monkeypatch, tmp_path)
    _healthy_radarr()
    _watchlist(fixture)

    assert main(["--check"]) == 0

    out = capsys.readouterr().out
    assert "All checks passed" in out
    assert "3 films" in out
    # The whole point: no state file, no writes to Radarr.
    assert not os.path.exists(state)
    assert not [c for c in responses.calls if c.request.method == "POST"]


@responses.activate
def test_check_reports_unreachable_radarr(monkeypatch, tmp_path, capsys):
    _env(monkeypatch, tmp_path)
    responses.add(responses.GET, f"{RADARR}/system/status", status=500)

    assert main(["--check"]) == 1

    out = capsys.readouterr().out
    assert "cannot reach Radarr" in out
    # The most common real cause gets named explicitly.
    assert "Docker network" in out


@responses.activate
def test_check_reports_wrong_quality_profile(monkeypatch, tmp_path, fixture, capsys):
    _env(monkeypatch, tmp_path, RADARR_QUALITY_PROFILE="Bluray-2160p")
    _healthy_radarr()
    _watchlist(fixture)

    assert main(["--check"]) == 1

    out = capsys.readouterr().out
    assert "quality profile" in out
    assert "HD-1080p" in out  # tells you the valid options


@responses.activate
def test_check_reports_a_private_watchlist(monkeypatch, tmp_path, capsys):
    _env(monkeypatch, tmp_path)
    _healthy_radarr()
    responses.add(responses.GET, f"{LB}/nickelliis/watchlist/", status=404)

    assert main(["--check"]) == 1

    out = capsys.readouterr().out
    assert "public" in out


@responses.activate
def test_bad_api_key_is_not_reported_as_unreachable(monkeypatch, tmp_path, fixture, capsys):
    """A 401 proves Radarr IS reachable; saying otherwise misdirects debugging."""
    _env(monkeypatch, tmp_path)
    responses.add(responses.GET, f"{RADARR}/system/status", status=401, body="Unauthorized")
    _watchlist(fixture)

    assert main(["--check"]) == 1

    out = capsys.readouterr().out
    assert "rejected the API key" in out
    assert "The URL is correct" in out
    # The misleading networking advice must not appear for an auth failure.
    assert "Docker network" not in out
    assert "cannot reach Radarr" not in out
