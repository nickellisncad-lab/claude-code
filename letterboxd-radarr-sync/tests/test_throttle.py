"""One-movie-at-a-time enforcement."""

import pytest

from lbxd_sync.radarr import RadarrError
from lbxd_sync.state import Store
from lbxd_sync.sync import Syncer
from tests.test_sync import (
    PARASITE,
    PERFECT,
    TMDB,
    ZONE,
    FakeLetterboxd,
    FakeRadarr,
    make_config,
)


@pytest.fixture
def store():
    with Store(":memory:") as s:
        yield s


def throttled(**overrides):
    """Config with the shipped defaults: one download, one add per pass."""
    base = dict(max_active_downloads=1, max_adds_per_run=1)
    base.update(overrides)
    return make_config(**base)


def _seeded(store, films):
    lb = FakeLetterboxd({"nickelliis": [], "karlasalgadox": []})
    radarr = FakeRadarr()
    syncer = Syncer(throttled(), lb, radarr, store)
    syncer.run_once()
    lb.watchlists["nickelliis"] = films
    return lb, radarr, syncer


def test_only_one_movie_is_added_per_pass(store):
    lb, radarr, syncer = _seeded(store, [PARASITE, ZONE, PERFECT])

    summary = syncer.run_once()

    assert len(radarr.added) == 1
    assert summary.added == 1
    assert summary.deferred == 2


def test_deferred_films_arrive_on_later_passes(store):
    lb, radarr, syncer = _seeded(store, [PARASITE, ZONE, PERFECT])

    syncer.run_once()
    syncer.run_once()
    syncer.run_once()

    assert sorted(radarr.added) == sorted(TMDB.values())


def test_nothing_is_added_while_a_download_is_running(store):
    """The literal requirement: one movie downloading at a time."""
    lb, radarr, syncer = _seeded(store, [PARASITE, ZONE])
    radarr.queue = 1  # Radarr is already downloading something.

    summary = syncer.run_once()

    assert radarr.added == []
    assert summary.added == 0
    assert summary.deferred == 2

    # Once that download finishes, the next pass proceeds.
    radarr.queue = 0
    summary = syncer.run_once()
    assert len(radarr.added) == 1


def test_unreadable_queue_fails_closed(store):
    """If we cannot tell what is downloading, do not start anything."""
    lb, radarr, syncer = _seeded(store, [PARASITE])
    radarr.queue_error = RadarrError("connection refused")

    summary = syncer.run_once()

    assert radarr.added == []
    assert summary.deferred == 1

    radarr.queue_error = None
    syncer.run_once()
    assert radarr.added == [TMDB["parasite-2019"]]


def test_deferred_film_is_not_starved_by_newer_additions(store):
    """A backlog item must not lose its slot to a fresh watchlist entry forever."""
    lb, radarr, syncer = _seeded(store, [PARASITE, ZONE])
    syncer.run_once()
    first = list(radarr.added)

    # Something new shows up before the deferred film gets its turn.
    lb.watchlists["nickelliis"] = [PARASITE, ZONE, PERFECT]
    syncer.run_once()

    assert len(radarr.added) == 2
    assert radarr.added != first + [TMDB["perfect-days-2023"]], (
        "the deferred film should go before the newly added one"
    )


def test_skips_and_errors_do_not_consume_the_download_slot(store):
    """A TV series cannot download, so it must not use up the single slot."""
    from lbxd_sync.letterboxd import Film

    tv = Film(slug="the-sopranos", title="The Sopranos", year=1999)
    lb, radarr, syncer = _seeded(store, [tv, PARASITE])

    summary = syncer.run_once()

    assert summary.skipped_no_tmdb == 1
    assert radarr.added == [TMDB["parasite-2019"]]
    assert summary.deferred == 0


def test_search_disabled_ignores_queue_depth_but_still_caps_adds(store):
    """With no search on add, nothing downloads, so queue depth is irrelevant."""
    lb = FakeLetterboxd({"nickelliis": [], "karlasalgadox": []})
    radarr = FakeRadarr(queue=5)
    config = throttled(search_on_add=False, max_adds_per_run=2)
    syncer = Syncer(config, lb, radarr, store)
    syncer.run_once()

    lb.watchlists["nickelliis"] = [PARASITE, ZONE, PERFECT]
    summary = syncer.run_once()

    assert len(radarr.added) == 2
    assert summary.deferred == 1


def test_unlimited_when_both_caps_are_zero(store):
    lb = FakeLetterboxd({"nickelliis": [], "karlasalgadox": []})
    radarr = FakeRadarr(queue=9)
    syncer = Syncer(make_config(), lb, radarr, store)
    syncer.run_once()

    lb.watchlists["nickelliis"] = [PARASITE, ZONE, PERFECT]
    summary = syncer.run_once()

    assert len(radarr.added) == 3
    assert summary.deferred == 0
