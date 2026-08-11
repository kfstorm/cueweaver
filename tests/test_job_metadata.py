import io
import json
from pathlib import Path
from threading import Event, Thread

from cueweaver.job import JobRunner, JobState
from cueweaver.metadata import Glossary, MetadataCache, TMDbMetadataProvider

SRT = """1
00:00:01,000 --> 00:00:02,000
Hello
"""


TRANSLATED = """1
00:00:01,000 --> 00:00:02,000
你好
"""


TRANSLATION_CONTEXT_INSTRUCTIONS = """## Translation Context

The following metadata is supplemental context for understanding the series and episode.

### How to use this context

- Source-language metadata is provided primarily for understanding the plot, characters, relationships, identities, and events.
- Target-language metadata is provided primarily as a reference for established localized names and terminology.
- Target-language metadata may be incomplete, inaccurate, written from an omniscient perspective, or describe information that characters do not yet know.
- Do not copy titles, ranks, relationships, institutions, or other terms from the target-language metadata unless they clearly refer to the same entity or concept in the current subtitle.
- Do not reveal a character's true identity, title, relationship, or future status unless the speaker knows it at this point in the story.
- Do not merge different people, institutions, ranks, or concepts merely because they have similar meanings.
- When metadata conflicts with the source subtitle or the immediate dialogue context, the source subtitle and dialogue context take precedence.

Use the following priority when resolving ambiguity:

1. Current source subtitle
2. Immediate subtitle/dialogue context"""

TRANSLATION_METADATA_PRIORITY = """3. Source-language episode metadata
4. Source-language series metadata
5. Target-language episode metadata
6. Target-language series metadata"""


def expected_context(*sections: str) -> str:
    instructions = (
        f"{TRANSLATION_CONTEXT_INSTRUCTIONS}\n{TRANSLATION_METADATA_PRIORITY}"
    )
    return f"{instructions}\n\n---\n\n" + "\n\n".join(sections)


class MetadataFixture:
    def __init__(self) -> None:
        self.series_calls: list[str] = []
        self.episode_calls: list[tuple[str, int, int]] = []
        self.series_overview = "The complete series overview."
        self.episode_overview = "The complete episode overview."

    def get_series_overview(self, series_id: str) -> str:
        self.series_calls.append(series_id)
        return self.series_overview

    def get_episode_overview(
        self, series_id: str, season_number: int, episode_number: int
    ) -> str:
        self.episode_calls.append((series_id, season_number, episode_number))
        return self.episode_overview


class BilingualMetadataFixture:
    def __init__(self) -> None:
        self.series_calls: list[tuple[str, str, str]] = []
        self.episode_calls: list[tuple[str, int, int, str, str]] = []
        self.values = {
            ("series", "title", "en"): "Dong Yi",
            ("series", "overview", "en"): "The source series overview.",
            ("series", "title", "zh"): "同伊",
            ("series", "overview", "zh"): "目标语言的剧集简介。",
            ("episode", "title", "en"): "The First Episode",
            ("episode", "overview", "en"): "The source episode overview.",
            ("episode", "title", "zh"): "第一集",
            ("episode", "overview", "zh"): "目标语言的单集简介。",
        }

    def get_series_wikidata_id(self, series_id: str) -> str | None:
        return "Q42"

    def get_glossary(self, series_id: str, target_language: str) -> Glossary:
        return Glossary()

    def get_series_title(self, series_id: str, language: str) -> str:
        self.series_calls.append(("title", series_id, language))
        return self.values[("series", "title", language)]

    def get_series_overview(self, series_id: str, language: str) -> str:
        self.series_calls.append(("overview", series_id, language))
        return self.values[("series", "overview", language)]

    def get_episode_title(
        self, series_id: str, season_number: int, episode_number: int, language: str
    ) -> str:
        self.episode_calls.append(
            ("title", series_id, season_number, episode_number, language)
        )
        return self.values[("episode", "title", language)]

    def get_episode_overview(
        self,
        series_id: str,
        season_number: int,
        episode_number: int,
        language: str,
    ) -> str:
        self.episode_calls.append(
            ("overview", series_id, season_number, episode_number, language)
        )
        return self.values[("episode", "overview", language)]


class ContextTranslator:
    def __init__(self) -> None:
        self.contexts: list[str] = []

    def translate(
        self, source: Path, target_language: str, *, context: str = ""
    ) -> str:
        self.contexts.append(context)
        return TRANSLATED


class FailingMetadata:
    def get_series_overview(self, series_id: str) -> str:
        raise RuntimeError("TMDb is unavailable")

    def get_episode_overview(
        self, series_id: str, season_number: int, episode_number: int
    ) -> str:
        raise AssertionError("episode metadata should not be requested")


class RetryableMetadata(MetadataFixture):
    def __init__(self) -> None:
        super().__init__()
        self.fail = True

    def get_series_overview(self, series_id: str) -> str:
        if self.fail:
            raise RuntimeError("temporary TMDb outage")
        return super().get_series_overview(series_id)


class RetryOnceMetadata(MetadataFixture):
    def __init__(self) -> None:
        super().__init__()
        self.failures = 1

    def get_series_overview(self, series_id: str) -> str:
        if self.failures:
            self.failures -= 1
            raise RuntimeError("temporary TMDb outage")
        return super().get_series_overview(series_id)


class QidMetadata(MetadataFixture):
    def get_series_wikidata_id(self, series_id: str) -> str | None:
        return "Q42" if series_id in {"1399", "alternate-id"} else None

    def get_glossary(self, series_id: str, target_language: str) -> Glossary:
        return Glossary()


class BlockingMetadata:
    def __init__(self) -> None:
        self.started = Event()
        self.released = Event()
        self.cancelled = False

    def get_series_overview(self, series_id: str) -> str:
        self.started.set()
        assert self.released.wait(timeout=5)
        return "unused series overview"

    def get_episode_overview(
        self, series_id: str, season_number: int, episode_number: int
    ) -> str:
        return "unused episode overview"

    def cancel(self) -> None:
        self.cancelled = True
        self.released.set()


class JsonResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def run_metadata_job(
    media,
    source,
    metadata,
    cache,
    *,
    translator=None,
    season_number=1,
    episode_number=2,
    refresh_metadata=False,
):
    return JobRunner(
        translator=translator or ContextTranslator(),
        metadata_provider=metadata,
        metadata_cache=cache,
    ).run(
        media,
        target_language="zh",
        source=source,
        series_id="1399",
        season_number=season_number,
        episode_number=episode_number,
        refresh_metadata=refresh_metadata,
    )


def start_metadata_job(runner, media, source):
    results = []
    thread = Thread(
        target=lambda: results.append(
            runner.run(
                media,
                target_language="zh",
                source=source,
                series_id="1399",
                season_number=1,
                episode_number=2,
            )
        )
    )
    thread.start()
    return results, thread


def create_metadata_fixture(tmp_path):
    media = tmp_path / "Movie.mkv"
    source = tmp_path / "Movie.en.srt"
    media.write_bytes(b"media")
    source.write_text(SRT, encoding="utf-8")
    return media, source


def test_metadata_context_is_gathered_before_translation(tmp_path):
    media = tmp_path / "Movie.mkv"
    source = tmp_path / "Movie.en.srt"
    media.write_bytes(b"media")
    source.write_text(SRT, encoding="utf-8")
    metadata = MetadataFixture()
    translator = ContextTranslator()

    result = JobRunner(
        translator=translator,
        metadata_provider=metadata,
        metadata_cache=MetadataCache(tmp_path / "metadata-cache"),
    ).run(
        media,
        target_language="zh",
        source=source,
        series_id="1399",
        season_number=1,
        episode_number=2,
    )

    assert result.state is JobState.PUBLISHED
    assert result.lifecycle == (
        JobState.DISCOVERED,
        JobState.METADATA,
        JobState.TRANSLATING,
        JobState.VALIDATING,
        JobState.PUBLISHING,
        JobState.PUBLISHED,
    )
    assert metadata.series_calls == ["1399"]
    assert metadata.episode_calls == [("1399", 1, 2)]
    assert translator.contexts == [
        expected_context(
            "TMDb series overview:\nThe complete series overview.",
            "TMDb episode overview (S01E02):\nThe complete episode overview.",
        )
    ]
    assert result.context == translator.contexts[0]


def test_no_metadata_fetch_keeps_translation_context_instructions(tmp_path):
    media, source = create_metadata_fixture(tmp_path)
    metadata = MetadataFixture()
    translator = ContextTranslator()

    result = JobRunner(
        translator=translator,
        metadata_provider=metadata,
        metadata_cache=MetadataCache(tmp_path / "metadata-cache"),
    ).run(
        media,
        target_language="zh",
        source=source,
        series_id="1399",
        season_number=1,
        episode_number=2,
        no_metadata_fetch=True,
    )

    assert result.state is JobState.PUBLISHED
    assert result.context == TRANSLATION_CONTEXT_INSTRUCTIONS
    assert translator.contexts == [TRANSLATION_CONTEXT_INSTRUCTIONS]
    assert "3. Source-language episode metadata" not in result.context
    assert metadata.series_calls == []
    assert metadata.episode_calls == []
    assert result.metadata_degradation is None


def test_metadata_context_contains_source_and_target_series_and_episode_values(
    tmp_path,
):
    media, source = create_metadata_fixture(tmp_path)
    metadata = BilingualMetadataFixture()
    translator = ContextTranslator()

    result = JobRunner(
        translator=translator,
        metadata_provider=metadata,
        metadata_cache=MetadataCache(tmp_path / "metadata-cache"),
    ).run(
        media,
        target_language="zh",
        source=source,
        series_id="1399",
        season_number=1,
        episode_number=2,
    )

    assert result.state is JobState.PUBLISHED
    assert translator.contexts == [
        expected_context(
            "Series title (source: en):\nDong Yi",
            "Series overview (source: en):\nThe source series overview.",
            "Series title (target: zh):\n同伊",
            "Series overview (target: zh):\n目标语言的剧集简介。",
            "Episode title (source: en) (S01E02):\nThe First Episode",
            "Episode overview (source: en) (S01E02):\nThe source episode overview.",
            "Episode title (target: zh) (S01E02):\n第一集",
            "Episode overview (target: zh) (S01E02):\n目标语言的单集简介。",
        )
    ]
    assert result.context == translator.contexts[0]
    assert result.metadata_request is not None
    assert result.metadata_request.source_language == "en"
    assert result.metadata_request.target_language == "zh"


def test_source_only_metadata_context_omits_target_fields(tmp_path):
    media, source = create_metadata_fixture(tmp_path)
    metadata = BilingualMetadataFixture()
    for key in (
        ("series", "title", "zh"),
        ("series", "overview", "zh"),
        ("episode", "title", "zh"),
        ("episode", "overview", "zh"),
    ):
        metadata.values[key] = ""
    translator = ContextTranslator()

    result = run_metadata_job(
        media,
        source,
        metadata,
        MetadataCache(tmp_path / "metadata-cache"),
        translator=translator,
    )

    assert result.state is JobState.PUBLISHED
    assert result.context == expected_context(
        "Series title (source: en):\nDong Yi",
        "Series overview (source: en):\nThe source series overview.",
        "Episode title (source: en) (S01E02):\nThe First Episode",
        "Episode overview (source: en) (S01E02):\nThe source episode overview.",
    )


def test_target_only_metadata_context_omits_source_fields(tmp_path):
    media, source = create_metadata_fixture(tmp_path)
    metadata = BilingualMetadataFixture()
    for key in (
        ("series", "title", "en"),
        ("series", "overview", "en"),
        ("episode", "title", "en"),
        ("episode", "overview", "en"),
    ):
        metadata.values[key] = ""
    translator = ContextTranslator()

    result = run_metadata_job(
        media,
        source,
        metadata,
        MetadataCache(tmp_path / "metadata-cache"),
        translator=translator,
    )

    assert result.state is JobState.PUBLISHED
    assert result.context == expected_context(
        "Series title (target: zh):\n同伊",
        "Series overview (target: zh):\n目标语言的剧集简介。",
        "Episode title (target: zh) (S01E02):\n第一集",
        "Episode overview (target: zh) (S01E02):\n目标语言的单集简介。",
    )


def test_mixed_metadata_context_only_renders_available_fields(tmp_path):
    media, source = create_metadata_fixture(tmp_path)
    metadata = BilingualMetadataFixture()
    for key in tuple(metadata.values):
        metadata.values[key] = ""
    metadata.values[("series", "overview", "en")] = "The source series overview."
    metadata.values[("episode", "overview", "zh")] = "目标语言的单集简介。"
    translator = ContextTranslator()

    result = run_metadata_job(
        media,
        source,
        metadata,
        MetadataCache(tmp_path / "metadata-cache"),
        translator=translator,
    )

    assert result.state is JobState.PUBLISHED
    assert result.context == expected_context(
        "Series overview (source: en):\nThe source series overview.",
        "Episode overview (target: zh) (S01E02):\n目标语言的单集简介。",
    )


def test_bilingual_metadata_cache_reuses_language_pair_and_refreshes_all_variants(
    tmp_path,
):
    media, source = create_metadata_fixture(tmp_path)
    metadata = BilingualMetadataFixture()
    cache = MetadataCache(tmp_path / "metadata-cache")

    first = run_metadata_job(media, source, metadata, cache)
    second = run_metadata_job(
        media,
        source,
        metadata,
        cache,
        season_number=2,
        episode_number=1,
    )
    metadata.values[("series", "title", "zh")] = "刷新后的同伊"
    refreshed = run_metadata_job(
        media,
        source,
        metadata,
        cache,
        translator=ContextTranslator(),
        refresh_metadata=True,
    )

    assert first.state is JobState.PUBLISHED
    assert second.state is JobState.PUBLISHED
    assert refreshed.state is JobState.PUBLISHED
    assert len(metadata.series_calls) == 8
    assert len(metadata.episode_calls) == 12
    assert "刷新后的同伊" in refreshed.context
    cache_payload = json.loads(
        next((tmp_path / "metadata-cache").glob("*.json")).read_text()
    )
    assert set(cache_payload["contexts"]) == {"en->zh"}
    assert set(cache_payload["contexts"]["en->zh"]["series"]["languages"]) == {
        "en",
        "zh",
    }
    assert next((tmp_path / "metadata-cache").glob("*.json")).name.startswith("Q42-")


def test_tmdb_provider_returns_full_series_and_episode_overviews(monkeypatch):
    requests = []
    responses = iter(
        [
            {"overview": "full series overview"},
            {"overview": "full episode overview"},
        ]
    )

    def urlopen(request, *, timeout):
        requests.append((request, timeout))
        return JsonResponse(json.dumps(next(responses)).encode("utf-8"))

    monkeypatch.setattr("cueweaver.metadata.urllib.request.urlopen", urlopen)
    provider = TMDbMetadataProvider(
        api_key="tmdb-key",
        base_url="https://tmdb.test/3",
    )

    series_overview = provider.get_series_overview("1399")
    episode_overview = provider.get_episode_overview("1399", 1, 2)

    assert series_overview == "full series overview"
    assert episode_overview == "full episode overview"
    assert len(requests) == 2
    assert "/tv/1399?language=en-US&api_key=tmdb-key" in requests[0][0].full_url
    assert "/tv/1399/season/1/episode/2?language=en-US&api_key=tmdb-key" in (
        requests[1][0].full_url
    )
    assert requests[0][1] == 30.0


def test_tmdb_provider_requests_titles_and_overviews_in_the_requested_language(
    monkeypatch,
):
    requests = []
    responses = iter(
        [
            {"name": "Dong Yi", "overview": "English overview"},
            {"name": "Dong Yi", "overview": "English overview"},
            {"name": "第一集", "overview": "中文单集简介"},
            {"name": "第一集", "overview": "中文单集简介"},
        ]
    )

    def urlopen(request, *, timeout):
        requests.append((request, timeout))
        return JsonResponse(json.dumps(next(responses)).encode("utf-8"))

    monkeypatch.setattr("cueweaver.metadata.urllib.request.urlopen", urlopen)
    provider = TMDbMetadataProvider(api_key="tmdb-key")

    assert provider.get_series_title("1399", "en") == "Dong Yi"
    assert provider.get_series_overview("1399", "en") == "English overview"
    assert provider.get_episode_title("1399", 1, 2, "zh-CN") == "第一集"
    assert provider.get_episode_overview("1399", 1, 2, "zh-CN") == "中文单集简介"
    assert [
        request[0].full_url.split("language=", 1)[1].split("&", 1)[0]
        for request in requests
    ] == ["en", "en", "zh-CN", "zh-CN"]


def test_missing_localized_metadata_is_visible_and_does_not_block_translation(tmp_path):
    media, source = create_metadata_fixture(tmp_path)
    metadata = BilingualMetadataFixture()
    metadata.values[("series", "overview", "zh")] = ""
    translator = ContextTranslator()

    result = JobRunner(
        translator=translator,
        metadata_provider=metadata,
        metadata_cache=MetadataCache(tmp_path / "metadata-cache"),
    ).run(
        media,
        target_language="zh",
        source=source,
        series_id="1399",
        season_number=1,
        episode_number=2,
    )

    assert result.state is JobState.PUBLISHED
    assert result.metadata_degradation is not None
    assert "series overview (target: zh)" in result.metadata_degradation
    assert "Series title (target: zh):\n同伊" in result.context
    assert "Series overview (target: zh)" not in result.context
    assert translator.contexts == [result.context]


def test_metadata_refresh_does_not_leave_stale_localized_values_in_cache(tmp_path):
    media, source = create_metadata_fixture(tmp_path)
    metadata = BilingualMetadataFixture()
    cache = MetadataCache(tmp_path / "metadata-cache")

    run_metadata_job(media, source, metadata, cache)
    metadata.values["series", "overview", "zh"] = ""
    refreshed = run_metadata_job(
        media,
        source,
        metadata,
        cache,
        refresh_metadata=True,
    )
    metadata.values["series", "overview", "zh"] = "重新获取的中文简介"
    recovered = run_metadata_job(media, source, metadata, cache)

    assert refreshed.metadata_degradation is not None
    assert "Series overview (target: zh)" not in refreshed.context
    assert "重新获取的中文简介" in recovered.context
    assert metadata.series_calls[-1] == ("overview", "1399", "zh")


def test_metadata_cache_reuses_series_context_across_episodes_and_jobs(tmp_path):
    media, source = create_metadata_fixture(tmp_path)
    metadata = MetadataFixture()
    cache = MetadataCache(tmp_path / "metadata-cache")

    first = run_metadata_job(media, source, metadata, cache)
    second = run_metadata_job(
        media,
        source,
        metadata,
        cache,
        season_number=2,
        episode_number=1,
    )
    third = run_metadata_job(media, source, metadata, cache)

    assert first.state is JobState.PUBLISHED
    assert second.state is JobState.PUBLISHED
    assert third.state is JobState.PUBLISHED
    assert metadata.series_calls == ["1399"]
    assert metadata.episode_calls == [
        ("1399", 1, 2),
        ("1399", 2, 1),
    ]


def test_metadata_cache_uses_series_qid_across_provider_identifiers(tmp_path):
    media, source = create_metadata_fixture(tmp_path)
    metadata = QidMetadata()
    cache = MetadataCache(tmp_path / "metadata-cache")

    first = JobRunner(
        translator=ContextTranslator(),
        metadata_provider=metadata,
        metadata_cache=cache,
    ).run(
        media,
        target_language="zh",
        source=source,
        series_id="1399",
        season_number=1,
        episode_number=2,
    )
    second = JobRunner(
        translator=ContextTranslator(),
        metadata_provider=metadata,
        metadata_cache=cache,
    ).run(
        media,
        target_language="zh",
        source=source,
        series_id="alternate-id",
        season_number=1,
        episode_number=2,
    )

    assert first.state is JobState.PUBLISHED
    assert second.state is JobState.PUBLISHED
    assert first.metadata_request is not None
    assert first.metadata_request.cache_key == "Q42"
    assert second.metadata_request is not None
    assert second.metadata_request.cache_key == "Q42"
    assert metadata.series_calls == ["1399"]
    assert metadata.episode_calls == [("1399", 1, 2)]
    cache_files = tuple((tmp_path / "metadata-cache").glob("*.json"))
    assert len(cache_files) == 1
    assert cache_files[0].name.startswith("Q42-")


def test_manual_metadata_refresh_bypasses_the_long_lived_cache(tmp_path):
    media, source = create_metadata_fixture(tmp_path)
    metadata = MetadataFixture()
    cache = MetadataCache(tmp_path / "metadata-cache")

    first = run_metadata_job(media, source, metadata, cache)
    metadata.series_overview = "The refreshed series overview."
    metadata.episode_overview = "The refreshed episode overview."
    translator = ContextTranslator()
    refreshed = run_metadata_job(
        media,
        source,
        metadata,
        cache,
        translator=translator,
        refresh_metadata=True,
    )

    assert first.state is JobState.PUBLISHED
    assert refreshed.state is JobState.PUBLISHED
    assert metadata.series_calls == ["1399", "1399"]
    assert metadata.episode_calls == [("1399", 1, 2), ("1399", 1, 2)]
    assert translator.contexts == [
        expected_context(
            "TMDb series overview:\nThe refreshed series overview.",
            "TMDb episode overview (S01E02):\nThe refreshed episode overview.",
        )
    ]


def test_missing_tmdb_credentials_degrade_to_baseline_translation(
    tmp_path, monkeypatch
):
    media = tmp_path / "Movie.mkv"
    source = tmp_path / "Movie.en.srt"
    media.write_bytes(b"media")
    source.write_text(SRT, encoding="utf-8")
    monkeypatch.delenv("CUEWEAVER_TMDB_API_KEY", raising=False)
    monkeypatch.delenv("TMDB_API_KEY", raising=False)
    translator = ContextTranslator()

    result = JobRunner(
        translator=translator,
        metadata_cache=MetadataCache(tmp_path / "metadata-cache"),
    ).run(
        media,
        target_language="zh",
        source=source,
        series_id="1399",
        season_number=1,
        episode_number=2,
    )

    assert result.state is JobState.PUBLISHED
    assert result.metadata_degradation is not None
    assert "TMDb API key is missing" in result.metadata_degradation
    assert result.context == TRANSLATION_CONTEXT_INSTRUCTIONS
    assert translator.contexts == [TRANSLATION_CONTEXT_INSTRUCTIONS]
    assert result.published_path is not None
    assert result.published_path.read_text(encoding="utf-8") == TRANSLATED


def test_metadata_refresh_requires_a_series_identifier_at_the_job_boundary(tmp_path):
    media = tmp_path / "Movie.mkv"
    source = tmp_path / "Movie.en.srt"
    media.write_bytes(b"media")
    source.write_text(SRT, encoding="utf-8")

    result = JobRunner(translator=ContextTranslator()).run(
        media,
        target_language="zh",
        source=source,
        refresh_metadata=True,
    )

    assert result.state is JobState.FAILED
    assert result.lifecycle == (JobState.FAILED,)
    assert result.error == (
        "A TMDb series ID is required with season, episode, or metadata refresh"
    )


def test_metadata_provider_failure_is_visible_without_blocking_translation(tmp_path):
    media = tmp_path / "Movie.mkv"
    source = tmp_path / "Movie.en.srt"
    media.write_bytes(b"media")
    source.write_text(SRT, encoding="utf-8")
    translator = ContextTranslator()

    result = JobRunner(
        translator=translator,
        metadata_provider=FailingMetadata(),
        metadata_cache=MetadataCache(tmp_path / "metadata-cache"),
    ).run(
        media,
        target_language="zh",
        source=source,
        series_id="1399",
        season_number=1,
        episode_number=2,
    )

    assert result.state is JobState.PUBLISHED
    assert result.metadata_degradation == "Metadata degraded: TMDb is unavailable"
    assert translator.contexts == [TRANSLATION_CONTEXT_INSTRUCTIONS]


def test_metadata_provider_hiccup_retries_before_translation(tmp_path):
    media, source = create_metadata_fixture(tmp_path)
    metadata = RetryOnceMetadata()
    translator = ContextTranslator()

    result = JobRunner(
        translator=translator,
        metadata_provider=metadata,
        metadata_cache=MetadataCache(tmp_path / "metadata-cache"),
    ).run(
        media,
        target_language="zh",
        source=source,
        series_id="1399",
        season_number=1,
        episode_number=2,
    )

    assert result.state is JobState.PUBLISHED
    assert result.metadata_degradation is None
    assert translator.contexts == [
        expected_context(
            "TMDb series overview:\nThe complete series overview.",
            "TMDb episode overview (S01E02):\nThe complete episode overview.",
        )
    ]


def test_metadata_retry_refreshes_context_without_repeating_translation(tmp_path):
    media = tmp_path / "Movie.mkv"
    source = tmp_path / "Movie.en.srt"
    media.write_bytes(b"media")
    source.write_text(SRT, encoding="utf-8")
    metadata = RetryableMetadata()
    translator = ContextTranslator()
    runner = JobRunner(
        translator=translator,
        metadata_provider=metadata,
        metadata_cache=MetadataCache(tmp_path / "metadata-cache"),
    )

    first = runner.run(
        media,
        target_language="zh",
        source=source,
        series_id="1399",
        season_number=1,
        episode_number=2,
    )
    metadata.fail = False
    retried = runner.retry_metadata(first)

    assert first.state is JobState.PUBLISHED
    assert first.metadata_degradation == "Metadata degraded: temporary TMDb outage"
    assert retried.state is JobState.PUBLISHED
    assert retried.metadata_degradation is None
    assert retried.context == expected_context(
        "TMDb series overview:\nThe complete series overview.",
        "TMDb episode overview (S01E02):\nThe complete episode overview.",
    )
    assert translator.contexts == [TRANSLATION_CONTEXT_INSTRUCTIONS]
    assert retried.published_path is not None
    assert retried.published_path.read_text(encoding="utf-8") == TRANSLATED


def test_cancel_during_metadata_is_terminal_and_never_starts_translation(tmp_path):
    media = tmp_path / "Movie.mkv"
    source = tmp_path / "Movie.en.srt"
    media.write_bytes(b"media")
    source.write_text(SRT, encoding="utf-8")
    metadata = BlockingMetadata()
    translator = ContextTranslator()
    runner = JobRunner(
        translator=translator,
        metadata_provider=metadata,
        metadata_cache=MetadataCache(tmp_path / "metadata-cache"),
    )
    results, thread = start_metadata_job(runner, media, source)
    assert metadata.started.wait(timeout=5)

    runner.cancel()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert metadata.cancelled is True
    assert len(results) == 1
    result = results[0]
    assert result.state is JobState.CANCELED
    assert result.lifecycle == (
        JobState.DISCOVERED,
        JobState.METADATA,
        JobState.CANCELED,
    )
    assert result.error == "Job canceled"
    assert translator.contexts == []
    assert result.published_path is None


def test_cancel_stops_a_blocking_tmdb_request_before_translation(tmp_path, monkeypatch):
    media = tmp_path / "Movie.mkv"
    source = tmp_path / "Movie.en.srt"
    media.write_bytes(b"media")
    source.write_text(SRT, encoding="utf-8")
    request_started = Event()
    release_request = Event()

    def urlopen(_request, *, timeout):
        request_started.set()
        assert release_request.wait(timeout=5)
        return JsonResponse(json.dumps({"overview": "late response"}).encode("utf-8"))

    monkeypatch.setattr("cueweaver.metadata.urllib.request.urlopen", urlopen)
    metadata = TMDbMetadataProvider(api_key="tmdb-key")
    translator = ContextTranslator()
    runner = JobRunner(
        translator=translator,
        metadata_provider=metadata,
        metadata_cache=MetadataCache(tmp_path / "metadata-cache"),
    )
    results, thread = start_metadata_job(runner, media, source)
    assert request_started.wait(timeout=5)

    runner.cancel()
    thread.join(timeout=2)
    release_request.set()

    assert not thread.is_alive()
    assert len(results) == 1
    assert results[0].state is JobState.CANCELED
    assert results[0].lifecycle == (
        JobState.DISCOVERED,
        JobState.METADATA,
        JobState.CANCELED,
    )
    assert translator.contexts == []
