"""End-to-end-ish tests for the sync pass, with Letterboxd and Radarr faked."""

import pytest

from lbxd_sync.config import Config
from lbxd_sync.letterboxd import Film, Watchlist, WatchlistUnavailable
from lbxd_sync.radarr import AddResult
from lbxd_sync.state import Store
from lbxd_sync.sync import Syncer

PARASITE = Film(slug="parasite-2019", title="Parasite", year=2019)
ZONE = Film(slug="the-zone-of-interest", title="The Zone of Interest", year=2023)
PERFECT = Film(slug="perfect-days-2023", title="Perfect Days", year=2023)

TMDB = {
    "parasite-2019": 496243,
    "the-zone-of-interest": 467244,
    "perfect-days-2023": 976893,
}


class FakeLetterboxd:
    def __init__(self, watchlists: dict[str, list[Film]]):
        self.watchlists = watchlists
        self.unavailable: set[str] = set()
        self.film_page_hits: list[str] = []

    def fetch_watchlist(self, username):
        if username in self.unavailable:
            raise WatchlistUnavailable(f"{username} is private")
        films = self.watchlists.get(username, [])
        return Watchlist(username=username, films=list(films), stated_total=len(films))

    def fetch_tmdb_id(self, slug):
        self.film_page_hits.append(slug)
        return TMDB.get(slug)


class FakeRadarr:
    def __init__(self, queue: int = 0):
        self.added: list[int] = []
        self.library: set[int] = set()
        self.next_id = 100
        self.queue = queue
        self.queue_error: Exception | None = None

    def queue_count(self):
        if self.queue_error:
            raise self.queue_error
        return self.queue

    def system_status(self):
        return {"version": "5.2.6"}

    def validate_root_folder(self, path):
        return None

    def resolve_quality_profile(self, wanted):
        return 4

    def ensure_tags(self, labels):
        return [1] if labels else []

    def add_movie(self, tmdb_id, **kwargs):
        if tmdb_id in self.library:
            return AddResult(tmdb_id, "already there", None, 1, already_existed=True)
        self.library.add(tmdb_id)
        self.added.append(tmdb_id)
        self.next_id += 1
        return AddResult(tmdb_id, f"movie-{tmdb_id}", 2019, self.next_id, False)


def make_config(**overrides) -> Config:
    base = dict(
        letterboxd_users=["nickelliis", "karlasalgadox"],
        radarr_url="http://radarr:7878",
        radarr_api_key="key",
        root_folder="/movies",
        quality_profile="HD-1080p",
        state_path=":memory:",
        seed_on_first_run=True,
        request_delay=0.0,
        max_active_downloads=0,
        max_adds_per_run=0,
    )
    base.update(overrides)
    return Config(**base)


@pytest.fixture
def store():
    with Store(":memory:") as s:
        yield s


def test_first_run_seeds_without_flooding_radarr(store):
    """A brand new install must not dump 192 existing films into Radarr."""
    lb = FakeLetterboxd({"nickelliis": [PARASITE, ZONE], "karlasalgadox": [PERFECT]})
    radarr = FakeRadarr()
    syncer = Syncer(make_config(), lb, radarr, store)

    summary = syncer.run_once()

    assert radarr.added == []
    assert summary.new_films == 0
    assert sorted(summary.seeded_users) == ["karlasalgadox", "nickelliis"]
    # And no film pages were fetched, so seeding is cheap.
    assert lb.film_page_hits == []


def test_films_added_after_seeding_are_requested(store):
    lb = FakeLetterboxd({"nickelliis": [PARASITE], "karlasalgadox": []})
    radarr = FakeRadarr()
    syncer = Syncer(make_config(), lb, radarr, store)
    syncer.run_once()

    lb.watchlists["nickelliis"] = [PARASITE, ZONE]
    summary = syncer.run_once()

    assert radarr.added == [TMDB["the-zone-of-interest"]]
    assert summary.added == 1
    assert summary.new_films == 1


def test_seed_disabled_imports_the_whole_watchlist(store):
    lb = FakeLetterboxd({"nickelliis": [PARASITE, ZONE], "karlasalgadox": []})
    radarr = FakeRadarr()
    syncer = Syncer(make_config(seed_on_first_run=False), lb, radarr, store)

    summary = syncer.run_once()

    assert sorted(radarr.added) == sorted([TMDB["parasite-2019"], TMDB["the-zone-of-interest"]])
    assert summary.added == 2


def test_film_on_both_watchlists_is_requested_once(store):
    lb = FakeLetterboxd({"nickelliis": [], "karlasalgadox": []})
    radarr = FakeRadarr()
    syncer = Syncer(make_config(), lb, radarr, store)
    syncer.run_once()

    lb.watchlists["nickelliis"] = [PARASITE]
    lb.watchlists["karlasalgadox"] = [PARASITE]
    summary = syncer.run_once()

    assert radarr.added == [TMDB["parasite-2019"]]
    assert summary.new_films == 1
    assert lb.film_page_hits.count("parasite-2019") == 1


def test_second_pass_does_not_re_request(store):
    lb = FakeLetterboxd({"nickelliis": [], "karlasalgadox": []})
    radarr = FakeRadarr()
    syncer = Syncer(make_config(), lb, radarr, store)
    syncer.run_once()

    lb.watchlists["nickelliis"] = [PARASITE]
    syncer.run_once()
    summary = syncer.run_once()

    assert radarr.added == [TMDB["parasite-2019"]]
    assert summary.new_films == 0


def test_state_survives_a_restart(tmp_path):
    """The whole point of the SQLite file: restarts must be quiet."""
    db = str(tmp_path / "state.db")
    lb = FakeLetterboxd({"nickelliis": [PARASITE], "karlasalgadox": []})
    radarr = FakeRadarr()

    with Store(db) as store:
        Syncer(make_config(state_path=db), lb, radarr, store).run_once()

    with Store(db) as store:
        summary = Syncer(make_config(state_path=db), lb, radarr, store).run_once()

    assert radarr.added == []
    assert summary.new_films == 0


def test_removing_then_re_adding_does_not_re_request(store):
    """It is already in Radarr, so a re-add should stay a no-op."""
    lb = FakeLetterboxd({"nickelliis": [], "karlasalgadox": []})
    radarr = FakeRadarr()
    syncer = Syncer(make_config(), lb, radarr, store)
    syncer.run_once()

    lb.watchlists["nickelliis"] = [PARASITE]
    syncer.run_once()
    lb.watchlists["nickelliis"] = []
    syncer.run_once()
    lb.watchlists["nickelliis"] = [PARASITE]
    summary = syncer.run_once()

    assert radarr.added == [TMDB["parasite-2019"]]
    assert summary.added == 0


def test_film_without_tmdb_id_is_skipped_not_retried(store):
    tv_show = Film(slug="the-sopranos", title="The Sopranos", year=1999)
    lb = FakeLetterboxd({"nickelliis": [], "karlasalgadox": []})
    radarr = FakeRadarr()
    syncer = Syncer(make_config(), lb, radarr, store)
    syncer.run_once()

    lb.watchlists["nickelliis"] = [tv_show]
    summary = syncer.run_once()

    assert summary.skipped_no_tmdb == 1
    assert radarr.added == []
    assert store.film_status("the-sopranos") == "no_tmdb_id"


def test_one_private_watchlist_does_not_stop_the_other(store):
    lb = FakeLetterboxd({"nickelliis": [], "karlasalgadox": []})
    radarr = FakeRadarr()
    syncer = Syncer(make_config(), lb, radarr, store)
    syncer.run_once()

    lb.unavailable.add("nickelliis")
    lb.watchlists["karlasalgadox"] = [PERFECT]
    summary = syncer.run_once()

    assert radarr.added == [TMDB["perfect-days-2023"]]
    assert "nickelliis" in summary.user_errors
    assert summary.ok is False


def test_dry_run_writes_nothing_to_radarr(store):
    lb = FakeLetterboxd({"nickelliis": [], "karlasalgadox": []})
    radarr = FakeRadarr()
    config = make_config(dry_run=True)
    syncer = Syncer(config, lb, radarr, store)
    syncer.run_once()

    lb.watchlists["nickelliis"] = [PARASITE]
    summary = syncer.run_once()

    assert radarr.added == []
    assert summary.added == 1
    assert store.film_status("parasite-2019") == "dry_run"


def test_dry_run_retries_on_the_next_real_pass(store):
    """A dry run must not poison state so the real run skips the film."""
    lb = FakeLetterboxd({"nickelliis": [], "karlasalgadox": []})
    radarr = FakeRadarr()
    Syncer(make_config(dry_run=True), lb, radarr, store).run_once()

    lb.watchlists["nickelliis"] = [PARASITE]
    Syncer(make_config(dry_run=True), lb, radarr, store).run_once()
    Syncer(make_config(), lb, radarr, store).run_once()

    assert radarr.added == [TMDB["parasite-2019"]]


def test_transient_radarr_error_is_retried_next_pass(store):
    """A film that errored must not be stranded just because it is no longer new."""
    from lbxd_sync.radarr import RadarrError

    lb = FakeLetterboxd({"nickelliis": [], "karlasalgadox": []})
    radarr = FakeRadarr()
    syncer = Syncer(make_config(), lb, radarr, store)
    syncer.run_once()

    def explode(tmdb_id, **kwargs):
        raise RadarrError("connection reset")

    working_add = radarr.add_movie
    radarr.add_movie = explode

    lb.watchlists["nickelliis"] = [PARASITE]
    failed_pass = syncer.run_once()
    assert failed_pass.failed == 1
    assert store.film_status("parasite-2019") == "error"

    radarr.add_movie = working_add
    recovered = syncer.run_once()

    assert radarr.added == [TMDB["parasite-2019"]]
    assert recovered.added == 1


def test_crash_between_seen_and_request_does_not_lose_the_film(store):
    """A film marked seen but never requested must still be recoverable."""
    lb = FakeLetterboxd({"nickelliis": [], "karlasalgadox": []})
    radarr = FakeRadarr()
    syncer = Syncer(make_config(), lb, radarr, store)
    syncer.run_once()

    def die(film, summary):
        raise KeyboardInterrupt("SIGKILL-ish")

    lb.watchlists["nickelliis"] = [PARASITE]
    syncer._request_film = die
    with pytest.raises(KeyboardInterrupt):
        syncer.run_once()

    # The slug is now recorded as seen, so it is no longer "new"...
    assert "parasite-2019" in store.seen_slugs("nickelliis")
    # ...but it is still pending, so the next pass picks it up anyway.
    assert store.film_status("parasite-2019") == "pending"

    syncer._request_film = Syncer._request_film.__get__(syncer)
    summary = syncer.run_once()

    assert radarr.added == [TMDB["parasite-2019"]]
    assert summary.added == 1
