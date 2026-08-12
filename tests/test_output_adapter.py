import pytest

from cueweaver.adapters.output import AtomicOutputPublisher
from cueweaver.application.errors import ServiceError


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
