from pathlib import Path

import pytest

from cueweaver.application.browsing import BrowseRequest, MediaBrowser
from cueweaver.application.errors import ServiceError


def test_browse_lists_directories_then_natural_sorted_media_with_nfo_labels(
    tmp_path: Path,
):
    root = tmp_path / "media"
    root.mkdir()
    (root / "Show 10").mkdir()
    (root / "Show 2").mkdir()
    (root / "Show 2" / "tvshow.nfo").write_text(
        "<tvshow><title>Example show</title><year>2024</year></tvshow>",
        encoding="utf-8",
    )
    (root / ".hidden").mkdir()
    (root / "Movie 10.mkv").write_bytes(b"media")
    (root / "Movie 2.MP4").write_bytes(b"media")
    (root / "notes.txt").write_bytes(b"not media")
    (root / ".hidden.mkv").write_bytes(b"hidden")
    (root / "Movie 2.nfo").write_text(
        "<movie><title>Displayed movie</title><year>2020</year></movie>",
        encoding="utf-8",
    )

    result = MediaBrowser(root).browse(BrowseRequest(Path(".")))

    assert [(entry.kind, entry.name) for entry in result.entries] == [
        ("directory", "Show 2"),
        ("directory", "Show 10"),
        ("media", "Movie 2.MP4"),
        ("media", "Movie 10.mkv"),
    ]
    assert result.entries[0].title == "Example show"
    assert result.entries[0].year == 2024
    assert result.entries[2].title == "Displayed movie"
    assert result.entries[2].year == 2020
    assert result.entries[2].path == Path("Movie 2.MP4")


def test_browse_uses_movie_nfo_after_invalid_media_nfo_and_never_tvshow_nfo(
    tmp_path: Path,
):
    root = tmp_path / "media"
    root.mkdir()
    (root / "Movie.mkv").write_bytes(b"media")
    (root / "Movie.nfo").write_text(
        "<movie><title>incomplete</title></movie>", encoding="utf-8"
    )
    (root / "movie.nfo").write_text(
        "<movie><title>Fallback title</title><year>1999</year></movie>",
        encoding="utf-8",
    )
    (root / "tvshow.nfo").write_text(
        "<tvshow><title>Wrong title</title><year>2000</year></tvshow>",
        encoding="utf-8",
    )
    (root / "Series").mkdir()
    (root / "Series" / "tvshow.nfo").write_text(
        "<tvshow><title>Series title</title><year>2010</year></tvshow>",
        encoding="utf-8",
    )

    result = MediaBrowser(root).browse(BrowseRequest(Path(".")))

    movie = next(entry for entry in result.entries if entry.name == "Movie.mkv")
    series = next(entry for entry in result.entries if entry.name == "Series")
    assert (movie.title, movie.year) == ("Fallback title", 1999)
    assert (series.title, series.year) == ("Series title", 2010)


def test_tvshow_nfo_never_labels_a_media_named_tvshow(tmp_path: Path):
    root = tmp_path / "media"
    root.mkdir()
    (root / "tvshow.mkv").write_bytes(b"media")
    (root / "tvshow.nfo").write_text(
        "<tvshow><title>Directory only</title><year>2024</year></tvshow>",
        encoding="utf-8",
    )

    entry = MediaBrowser(root).browse(BrowseRequest(Path("."))).entries[0]

    assert entry.title is None
    assert entry.year is None


@pytest.mark.parametrize("requested", ["../outside", "/tmp", "Media/../outside"])
def test_browse_rejects_paths_outside_the_media_root(tmp_path: Path, requested: str):
    root = tmp_path / "media"
    root.mkdir()

    with pytest.raises(ServiceError, match="Media path"):
        MediaBrowser(root).browse(BrowseRequest(Path(requested)))


def test_browse_omits_symlinks_that_escape_and_allows_symlinks_inside(tmp_path: Path):
    root = tmp_path / "media"
    root.mkdir()
    (root / "inside").mkdir()
    (root / "inside" / "Movie.mkv").write_bytes(b"media")
    outside = tmp_path / "outside.mkv"
    outside.write_bytes(b"secret")
    (root / "outside.mkv").symlink_to(outside)
    (root / "linked.mkv").symlink_to(root / "inside" / "Movie.mkv")
    (root / "linked-directory").symlink_to(root / "inside", target_is_directory=True)

    result = MediaBrowser(root).browse(BrowseRequest(Path(".")))

    assert [entry.name for entry in result.entries] == [
        "inside",
        "linked-directory",
        "linked.mkv",
    ]


def test_browse_keeps_relative_symlink_path_when_opening_directory(tmp_path: Path):
    root = tmp_path / "media"
    root.mkdir()
    (root / "target").mkdir()
    (root / "target" / "Movie.mkv").write_bytes(b"media")
    (root / "alias").symlink_to(root / "target", target_is_directory=True)

    result = MediaBrowser(root).browse(BrowseRequest(Path("alias")))

    assert result.path == Path("alias")
    assert result.entries[0].path == Path("alias/Movie.mkv")


def test_browse_ignores_unsafe_and_oversized_nfo(tmp_path: Path):
    root = tmp_path / "media"
    root.mkdir()
    (root / "Movie.mkv").write_bytes(b"media")
    (root / "Movie.nfo").write_text(
        "<!DOCTYPE movie [<!ENTITY xxe SYSTEM 'file:///secret'>]>"
        "<movie><title>&xxe;</title><year>2024</year></movie>",
        encoding="utf-8",
    )
    (root / "movie.nfo").write_bytes(b"<movie>" + b"x" * (1024 * 1024) + b"</movie>")

    entry = MediaBrowser(root).browse(BrowseRequest(Path("."))).entries[0]

    assert entry.title is None
    assert entry.year is None


def test_browse_ignores_utf16_unsafe_nfo(tmp_path: Path):
    root = tmp_path / "media"
    root.mkdir()
    (root / "Movie.mkv").write_bytes(b"media")
    (root / "Movie.nfo").write_text(
        "<!DOCTYPE movie [<!ENTITY xxe SYSTEM 'file:///secret'>]>"
        "<movie><title>&xxe;</title><year>2024</year></movie>",
        encoding="utf-16",
    )

    entry = MediaBrowser(root).browse(BrowseRequest(Path("."))).entries[0]

    assert entry.title is None
    assert entry.year is None


def test_browse_ignores_nfo_symlinked_outside_the_media_root(tmp_path: Path):
    root = tmp_path / "media"
    root.mkdir()
    (root / "Movie.mkv").write_bytes(b"media")
    outside = tmp_path / "Movie.nfo"
    outside.write_text(
        "<movie><title>Secret title</title><year>2024</year></movie>",
        encoding="utf-8",
    )
    (root / "Movie.nfo").symlink_to(outside)

    entry = MediaBrowser(root).browse(BrowseRequest(Path("."))).entries[0]

    assert entry.title is None
    assert entry.year is None
