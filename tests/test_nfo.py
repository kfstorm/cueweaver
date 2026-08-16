from cueweaver.application.browsing.nfo import NfoMetadata, parse_nfo


def test_parse_nfo_returns_movie_metadata_from_bytes():
    metadata = parse_nfo(
        b"<movie><title>Movie title</title><premiered>2024-05-01</premiered></movie>",
        "media",
    )

    assert metadata == NfoMetadata("Movie title", year=2024)


def test_parse_nfo_returns_episode_metadata_without_year():
    metadata = parse_nfo(
        b"<episodedetails><title>Episode title</title><season>2</season>"
        b"<episode>7</episode><year>2024</year></episodedetails>",
        "media",
    )

    assert metadata == NfoMetadata("Episode title", season=2, episode=7)


def test_parse_nfo_rejects_wrong_root_kind():
    assert (
        parse_nfo(
            b"<tvshow><title>Show title</title><year>2024</year></tvshow>",
            "media",
        )
        is None
    )


def test_parse_nfo_rejects_unsafe_oversized_and_unknown_encoding_bytes():
    unsafe = (
        b"<!DOCTYPE movie [<!ENTITY xxe SYSTEM 'file:///secret'>]>"
        b"<movie><title>&xxe;</title><year>2024</year></movie>"
    )
    oversized = b"<movie>" + b"x" * (1024 * 1024) + b"</movie>"
    unknown_encoding = (
        b'<?xml version="1.0" encoding="does-not-exist"?>'
        b"<movie><title>Broken</title><year>2024</year></movie>"
    )

    assert parse_nfo(unsafe, "media") is None
    assert parse_nfo(oversized, "media") is None
    assert parse_nfo(unknown_encoding, "media") is None
