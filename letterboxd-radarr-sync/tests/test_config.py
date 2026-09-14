import pytest

from lbxd_sync.config import Config, ConfigError

REQUIRED = {
    "LETTERBOXD_USERS": "nickelliis,karlasalgadox",
    "RADARR_URL": "http://radarr:7878/",
    "RADARR_API_KEY": "key",
    "RADARR_ROOT_FOLDER": "/movies",
    "RADARR_QUALITY_PROFILE": "HD-1080p",
}


@pytest.fixture
def env(monkeypatch):
    def _set(**overrides):
        for key in list(REQUIRED) + [
            "POLL_INTERVAL", "DRY_RUN", "RADARR_TAGS", "SEED_ON_FIRST_RUN",
            "RADARR_MINIMUM_AVAILABILITY", "RADARR_MONITOR", "LOG_LEVEL",
        ]:
            monkeypatch.delenv(key, raising=False)
        for key, value in {**REQUIRED, **overrides}.items():
            monkeypatch.setenv(key, value)

    return _set


def test_parses_a_valid_environment(env):
    env(RADARR_TAGS="letterboxd, karla", POLL_INTERVAL="900", DRY_RUN="true")
    config = Config.from_env()

    assert config.letterboxd_users == ["nickelliis", "karlasalgadox"]
    assert config.radarr_url == "http://radarr:7878"  # trailing slash stripped
    assert config.tags == ["letterboxd", "karla"]
    assert config.poll_interval == 900
    assert config.dry_run is True


def test_missing_required_setting(env, monkeypatch):
    env()
    monkeypatch.delenv("RADARR_API_KEY")

    with pytest.raises(ConfigError, match="RADARR_API_KEY is required"):
        Config.from_env()


def test_no_users_is_an_error(env):
    env(LETTERBOXD_USERS="  ")

    with pytest.raises(ConfigError, match="LETTERBOXD_USERS is required"):
        Config.from_env()


def test_username_must_look_like_a_username(env):
    """Usernames go straight into a URL path; reject anything that could escape."""
    env(LETTERBOXD_USERS="nickelliis,../../admin")

    with pytest.raises(ConfigError, match="invalid Letterboxd username"):
        Config.from_env()


def test_rejects_bad_minimum_availability(env):
    env(RADARR_MINIMUM_AVAILABILITY="whenever")

    with pytest.raises(ConfigError, match="RADARR_MINIMUM_AVAILABILITY"):
        Config.from_env()


def test_rejects_hammering_poll_interval(env):
    env(POLL_INTERVAL="5")

    with pytest.raises(ConfigError, match="at least 60 seconds"):
        Config.from_env()


def test_rejects_non_boolean(env):
    env(DRY_RUN="maybe")

    with pytest.raises(ConfigError, match="must be a boolean"):
        Config.from_env()
