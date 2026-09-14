import pytest
import responses

from lbxd_sync.letterboxd import (
    LetterboxdClient,
    WatchlistParseError,
    WatchlistUnavailable,
    extract_tmdb_id,
    parse_watchlist_page,
)


def test_parses_current_markup(fixture):
    films, last_page, total = parse_watchlist_page(fixture("watchlist_page1.html"))

    assert [f.slug for f in films] == ["parasite-2019", "the-zone-of-interest"]
    assert films[0].title == "Parasite"
    assert films[0].year == 2019
    assert films[0].url == "https://letterboxd.com/film/parasite-2019/"
    assert last_page == 2
    assert total == 3


def test_parses_legacy_markup(fixture):
    """The pre-2024 grid should still work if Letterboxd ever serves it."""
    films, _, _ = parse_watchlist_page(fixture("watchlist_legacy.html"))

    assert [f.slug for f in films] == ["stalker"]


def test_empty_watchlist_is_not_an_error(fixture):
    films, last_page, total = parse_watchlist_page(fixture("watchlist_empty.html"))

    assert films == []
    assert last_page is None
    assert total == 0


def test_extract_tmdb_id(fixture):
    assert extract_tmdb_id(fixture("film_page.html")) == 496243


def test_extract_tmdb_id_ignores_tv_links(fixture):
    """A TV series has no TMDB *movie* id, so Radarr must not be handed one."""
    assert extract_tmdb_id(fixture("film_page_tv.html")) is None


@responses.activate
def test_fetch_watchlist_follows_pagination(fixture):
    responses.add(
        responses.GET,
        "https://letterboxd.com/nickelliis/watchlist/",
        body=fixture("watchlist_page1.html"),
    )
    responses.add(
        responses.GET,
        "https://letterboxd.com/nickelliis/watchlist/page/2/",
        body=fixture("watchlist_page2.html"),
    )

    client = LetterboxdClient(user_agent="test", delay=0)
    watchlist = client.fetch_watchlist("nickelliis")

    assert [f.slug for f in watchlist.films] == [
        "parasite-2019",
        "the-zone-of-interest",
        "perfect-days-2023",
    ]
    assert watchlist.stated_total == 3


@responses.activate
def test_unknown_markup_raises_instead_of_reporting_empty(fixture):
    """The important failure mode: never silently decide a watchlist is empty."""
    responses.add(
        responses.GET,
        "https://letterboxd.com/nickelliis/watchlist/",
        body=fixture("watchlist_unknown_markup.html"),
    )

    client = LetterboxdClient(user_agent="test", delay=0)
    with pytest.raises(WatchlistParseError, match="markup has probably changed"):
        client.fetch_watchlist("nickelliis")


@responses.activate
def test_missing_user_raises_unavailable():
    responses.add(
        responses.GET,
        "https://letterboxd.com/nope/watchlist/",
        status=404,
    )

    client = LetterboxdClient(user_agent="test", delay=0)
    with pytest.raises(WatchlistUnavailable):
        client.fetch_watchlist("nope")


@responses.activate
def test_retries_then_succeeds_on_server_error(fixture):
    responses.add(
        responses.GET, "https://letterboxd.com/nickelliis/watchlist/", status=503
    )
    responses.add(
        responses.GET,
        "https://letterboxd.com/nickelliis/watchlist/",
        body=fixture("watchlist_empty.html"),
    )

    client = LetterboxdClient(user_agent="test", delay=0, max_retries=3)
    # Retry-After of 0 keeps the test fast.
    responses.registered()[0].headers = {"Retry-After": "0"}
    watchlist = client.fetch_watchlist("nickelliis")

    assert watchlist.films == []


def test_tmdb_id_comes_from_the_film_footer_not_elsewhere(fixture):
    """A TMDB link elsewhere on the page must not be mistaken for this film."""
    html = fixture("film_page.html").replace(
        "<body>",
        '<body><section class="related"><a href="https://www.themoviedb.org/movie/999999/">Other</a></section>',
    )

    assert extract_tmdb_id(html) == 496243


def test_multi_valued_attributes_do_not_crash_the_parser():
    """BeautifulSoup returns a list for some attributes; .strip() would raise."""
    html = """
    <h1>someone wants to see 1 film</h1>
    <ul class="grid"><li class="griditem">
      <div class="react-component poster" data-item-slug="dune-part-two"
           data-item-name="Dune: Part Two"
           data-item-full-display-name="Dune: Part Two (2024)"></div>
    </li></ul>
    """
    films, _, _ = parse_watchlist_page(html)

    assert films[0].slug == "dune-part-two"
    assert films[0].year == 2024


@responses.activate
def test_pagination_continues_when_the_paginator_is_missing(fixture):
    """If the paginator markup changes, fall back to the header count.

    Otherwise a 148-film watchlist silently syncs only its first page.
    """
    page1 = fixture("watchlist_page1.html").replace('class="pagination"', 'class="gone"')
    page2 = fixture("watchlist_page2.html").replace('class="pagination"', 'class="gone"')
    responses.add(responses.GET, "https://letterboxd.com/nickelliis/watchlist/", body=page1)
    responses.add(
        responses.GET, "https://letterboxd.com/nickelliis/watchlist/page/2/", body=page2
    )

    client = LetterboxdClient(user_agent="test", delay=0)
    watchlist = client.fetch_watchlist("nickelliis")

    assert len(watchlist.films) == 3


@responses.activate
def test_repeated_page_content_terminates_the_scrape(fixture):
    """An out-of-range page that echoes earlier content must not loop forever."""
    page1 = fixture("watchlist_page1.html").replace('class="pagination"', 'class="gone"')
    responses.add(responses.GET, "https://letterboxd.com/nickelliis/watchlist/", body=page1)
    for _ in range(10):
        responses.add(
            responses.GET, "https://letterboxd.com/nickelliis/watchlist/page/2/", body=page1
        )

    client = LetterboxdClient(user_agent="test", delay=0)
    watchlist = client.fetch_watchlist("nickelliis")

    assert len(watchlist.films) == 2
