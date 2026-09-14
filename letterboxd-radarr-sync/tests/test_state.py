import os

import pytest

from lbxd_sync.state import Store


@pytest.fixture
def store():
    with Store(":memory:") as s:
        yield s


def test_seen_slugs_round_trip(store):
    store.record_seen("nickelliis", ["a", "b"])

    assert store.seen_slugs("nickelliis") == {"a", "b"}
    assert store.seen_slugs("karlasalgadox") == set()


def test_record_seen_is_idempotent(store):
    store.record_seen("nickelliis", ["a"])
    store.record_seen("nickelliis", ["a", "b"])

    assert store.seen_slugs("nickelliis") == {"a", "b"}


def test_forget_seen(store):
    store.record_seen("nickelliis", ["a", "b"])
    store.forget_seen("nickelliis", ["a"])

    assert store.seen_slugs("nickelliis") == {"b"}


def test_seeded_flag(store):
    assert store.is_seeded("nickelliis") is False
    store.mark_seeded("nickelliis")
    assert store.is_seeded("nickelliis") is True


def test_done_and_retryable_statuses(store):
    store.record_film("added-film", status="added", tmdb_id=1)
    store.record_film("tv-show", status="no_tmdb_id")
    store.record_film("errored", status="error", detail="boom")
    store.record_film("pretend", status="dry_run")

    assert store.is_done("added-film") is True
    assert store.is_done("tv-show") is True
    assert store.is_done("errored") is False
    assert store.retryable_slugs() == {"errored", "pretend"}


def test_record_film_keeps_known_fields_on_update(store):
    store.record_film("f", status="error", title="Parasite", year=2019, tmdb_id=496243)
    store.record_film("f", status="added", radarr_id=77)

    row = store.conn.execute("SELECT * FROM films WHERE slug = 'f'").fetchone()
    assert row["status"] == "added"
    assert row["title"] == "Parasite"
    assert row["tmdb_id"] == 496243
    assert row["radarr_id"] == 77
    assert row["detail"] is None


def test_unopenable_state_path_gives_an_actionable_error(tmp_path):
    """A raw sqlite traceback tells you nothing about the real cause.

    In Docker this is almost always a bind-mounted ./data owned by root while
    the container runs unprivileged. Pointing at a directory reproduces the
    same sqlite failure without depending on the test user's privileges --
    chmod-based permission tests are meaningless when running as root.
    """
    from lbxd_sync.state import StateError

    with pytest.raises(StateError) as excinfo:
        Store(str(tmp_path))  # a directory, not a file

    message = str(excinfo.value)
    assert "cannot open the state database" in message
    assert "chown" in message
    assert "uid" in message


@pytest.mark.skipif(os.getuid() == 0, reason="root bypasses file permissions")
def test_unwritable_directory_gives_the_same_error(tmp_path):
    from lbxd_sync.state import StateError

    locked = tmp_path / "nowrite"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        with pytest.raises(StateError, match="chown"):
            Store(str(locked / "state.db"))
    finally:
        locked.chmod(0o700)
