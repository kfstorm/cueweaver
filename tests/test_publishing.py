from cueweaver.publishing import publish_atomically


def test_atomic_publishing_leaves_a_complete_destination(tmp_path):
    destination = tmp_path / "Movie.zh.srt"
    destination.write_bytes(b"old artifact")

    publish_atomically(b"complete new artifact", destination)

    assert destination.read_bytes() == b"complete new artifact"
    assert list(tmp_path.glob(f".{destination.name}.*.tmp")) == []
