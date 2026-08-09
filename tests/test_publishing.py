import pytest

from cueweaver.publishing import publish_atomically


def test_atomic_publishing_leaves_a_complete_destination(tmp_path):
    destination = tmp_path / "Movie.zh.srt"
    destination.write_bytes(b"old artifact")

    publish_atomically(b"complete new artifact", destination)

    assert destination.read_bytes() == b"complete new artifact"
    assert list(tmp_path.glob(f".{destination.name}.*.tmp")) == []


def test_atomic_publishing_failure_keeps_old_destination_and_cleans_temp(
    tmp_path, monkeypatch
):
    destination = tmp_path / "Movie.zh.srt"
    destination.write_bytes(b"old artifact")

    def fail_replace(_temporary_path, _destination):
        raise OSError("disk full")

    monkeypatch.setattr("cueweaver.publishing.os.replace", fail_replace)

    with pytest.raises(OSError, match="disk full"):
        publish_atomically(b"incomplete new artifact", destination)

    assert destination.read_bytes() == b"old artifact"
    assert list(tmp_path.glob(f".{destination.name}.*.tmp")) == []
