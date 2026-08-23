import pytest

from cueweaver.adapters.output import AtomicOutputPublisher
from cueweaver.application.errors import ServiceError


def fail_directory_sync(_directory):
    raise OSError("sync failed")


def publish_expect_write_failure(output, write, *, overwrite=False):
    with pytest.raises(ServiceError, match="Output cannot be written"):
        AtomicOutputPublisher().publish(output, write, overwrite=overwrite)


def test_output_publisher_creates_parent_and_publishes_without_overwriting(tmp_path):
    output = tmp_path / "nested" / "Movie.srt"

    AtomicOutputPublisher().publish(
        output, lambda temporary: temporary.write_text("subtitle")
    )

    assert output.read_text() == "subtitle"
    with pytest.raises(ServiceError) as error:
        AtomicOutputPublisher().publish(output, lambda _temporary: None)
    assert error.value.error_code == "output_exists"


def test_output_publisher_removes_temporary_file_when_publishing_fails(
    tmp_path, monkeypatch
):
    output = tmp_path / "Movie.zh.srt"
    monkeypatch.setattr(
        "cueweaver.adapters.output.os.link",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )

    with pytest.raises(ServiceError, match="Output cannot be written"):
        AtomicOutputPublisher().publish(
            output, lambda temporary: temporary.write_bytes(b"translated")
        )

    assert not output.exists()
    assert list(tmp_path.iterdir()) == []


def test_output_publisher_preserves_no_overwrite_when_link_races(tmp_path, monkeypatch):
    output = tmp_path / "Movie.srt"
    monkeypatch.setattr(
        "cueweaver.adapters.output.os.link",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(FileExistsError()),
    )

    with pytest.raises(ServiceError) as error:
        AtomicOutputPublisher().publish(
            output, lambda temporary: temporary.write_bytes(b"subtitle")
        )

    assert error.value.error_code == "output_exists"
    assert list(tmp_path.iterdir()) == []


def test_output_publisher_atomically_overwrites_only_after_write_succeeds(tmp_path):
    output = tmp_path / "Movie.srt"
    output.write_text("old")

    AtomicOutputPublisher().publish(
        output, lambda temporary: temporary.write_text("new"), overwrite=True
    )

    assert output.read_text() == "new"


def test_output_publisher_preserves_old_output_when_overwrite_write_fails(tmp_path):
    output = tmp_path / "Movie.srt"
    output.write_text("old")

    def fail_write(temporary):
        temporary.write_text("partial")
        raise OSError("disk full")

    with pytest.raises(ServiceError) as error:
        AtomicOutputPublisher().publish(output, fail_write, overwrite=True)

    assert error.value.error_code == "output_write_failed"
    assert output.read_text() == "old"
    assert list(tmp_path.iterdir()) == [output]


def test_output_publisher_rolls_back_when_directory_sync_fails(tmp_path, monkeypatch):
    output = tmp_path / "Movie.srt"
    output.write_text("old")
    monkeypatch.setattr(
        "cueweaver.adapters.output._fsync_directory", fail_directory_sync
    )
    publish_expect_write_failure(
        output, lambda temporary: temporary.write_text("new"), overwrite=True
    )

    assert output.read_text() == "old"
    assert list(tmp_path.iterdir()) == [output]


def test_output_publisher_removes_new_output_when_directory_sync_fails(
    tmp_path, monkeypatch
):
    output = tmp_path / "Movie.srt"
    monkeypatch.setattr(
        "cueweaver.adapters.output._fsync_directory", fail_directory_sync
    )
    publish_expect_write_failure(output, lambda temporary: temporary.write_text("new"))

    assert not output.exists()
    assert list(tmp_path.iterdir()) == []


def test_output_publisher_keeps_backup_when_rollback_fails(tmp_path, monkeypatch):
    output = tmp_path / "Movie.srt"
    output.write_text("old")
    original_replace = type(output).replace

    def fail_backup_replace(source, destination):
        if source.name.startswith("tmp"):
            raise OSError("rollback failed")
        return original_replace(source, destination)

    monkeypatch.setattr(type(output), "replace", fail_backup_replace)
    monkeypatch.setattr(
        "cueweaver.adapters.output._fsync_directory", fail_directory_sync
    )
    publish_expect_write_failure(
        output, lambda temporary: temporary.write_text("new"), overwrite=True
    )

    assert output.read_text() == "new"
    assert len(list(tmp_path.iterdir())) == 2
