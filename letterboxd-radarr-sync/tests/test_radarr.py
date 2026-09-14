import pytest
import responses

from lbxd_sync.radarr import MovieAlreadyExists, RadarrClient, RadarrError

BASE = "http://radarr:7878"
API = f"{BASE}/api/v3"


@pytest.fixture
def client():
    return RadarrClient(BASE, "secret-key")


@responses.activate
def test_api_key_is_sent_as_a_header(client):
    responses.add(responses.GET, f"{API}/system/status", json={"version": "5.2.6"})

    assert client.system_status()["version"] == "5.2.6"
    assert responses.calls[0].request.headers["X-Api-Key"] == "secret-key"
    assert "secret-key" not in responses.calls[0].request.url


@responses.activate
def test_resolve_quality_profile_by_name(client):
    responses.add(
        responses.GET,
        f"{API}/qualityprofile",
        json=[{"id": 4, "name": "HD-1080p"}, {"id": 6, "name": "Ultra-HD"}],
    )

    assert client.resolve_quality_profile("hd-1080p") == 4


@responses.activate
def test_resolve_quality_profile_by_id(client):
    responses.add(
        responses.GET, f"{API}/qualityprofile", json=[{"id": 4, "name": "HD-1080p"}]
    )

    assert client.resolve_quality_profile("4") == 4


@responses.activate
def test_unknown_quality_profile_lists_the_real_ones(client):
    responses.add(
        responses.GET, f"{API}/qualityprofile", json=[{"id": 4, "name": "HD-1080p"}]
    )

    with pytest.raises(RadarrError, match="available: HD-1080p"):
        client.resolve_quality_profile("Bluray-2160p")


@responses.activate
def test_validate_root_folder(client):
    responses.add(
        responses.GET, f"{API}/rootfolder", json=[{"id": 1, "path": "/movies"}]
    )

    client.validate_root_folder("/movies")

    with pytest.raises(RadarrError, match="not a Radarr root folder"):
        client.validate_root_folder("/films")


@responses.activate
def test_bad_api_key_is_reported_clearly(client):
    responses.add(responses.GET, f"{API}/system/status", status=401, body="Unauthorized")

    with pytest.raises(RadarrError, match="rejected the API key"):
        client.system_status()


@responses.activate
def test_add_movie_posts_the_expected_payload(client):
    responses.add(responses.GET, f"{API}/movie", json=[])
    responses.add(
        responses.GET,
        f"{API}/movie/lookup/tmdb",
        json={
            "title": "Parasite",
            "year": 2019,
            "titleSlug": "parasite-496243",
            "tmdbId": 496243,
            "images": [{"coverType": "poster"}],
        },
    )
    responses.add(
        responses.POST,
        f"{API}/movie",
        json={"id": 77, "title": "Parasite", "year": 2019},
        status=201,
    )

    result = client.add_movie(
        496243,
        root_folder="/movies",
        quality_profile_id=4,
        minimum_availability="released",
        monitored=True,
        search_on_add=True,
        tag_ids=[3],
    )

    assert result.radarr_id == 77
    assert result.already_existed is False

    posted = responses.calls[-1].request.body
    import json

    payload = json.loads(posted)
    assert payload["tmdbId"] == 496243
    assert payload["rootFolderPath"] == "/movies"
    assert payload["qualityProfileId"] == 4
    assert payload["minimumAvailability"] == "released"
    assert payload["monitored"] is True
    assert payload["tags"] == [3]
    assert payload["addOptions"] == {"searchForMovie": True}


@responses.activate
def test_add_movie_short_circuits_when_already_in_library(client):
    responses.add(
        responses.GET,
        f"{API}/movie",
        json=[{"id": 12, "title": "Parasite", "year": 2019}],
    )

    result = client.add_movie(
        496243, root_folder="/movies", quality_profile_id=4
    )

    assert result.already_existed is True
    assert result.radarr_id == 12
    # No lookup and no POST should have happened.
    assert len(responses.calls) == 1


@responses.activate
def test_duplicate_add_raises_movie_already_exists(client):
    responses.add(responses.GET, f"{API}/movie", json=[])
    responses.add(
        responses.GET, f"{API}/movie/lookup/tmdb", json={"title": "Parasite", "tmdbId": 496243}
    )
    responses.add(
        responses.POST,
        f"{API}/movie",
        status=400,
        json=[{"errorMessage": "This movie has already been added", "errorCode": "MovieExistsValidator"}],
    )

    with pytest.raises(MovieAlreadyExists):
        client.add_movie(496243, root_folder="/movies", quality_profile_id=4)


@responses.activate
def test_ensure_tags_creates_missing_ones(client):
    responses.add(responses.GET, f"{API}/tag", json=[{"id": 1, "label": "letterboxd"}])
    responses.add(responses.POST, f"{API}/tag", json={"id": 9, "label": "karla"})

    assert client.ensure_tags(["letterboxd", "karla"]) == [1, 9]
