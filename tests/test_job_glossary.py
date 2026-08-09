import io
import json
from pathlib import Path
from threading import Event, Thread

from cueweaver.job import JobRunner, JobState
from cueweaver.metadata import (
    Glossary,
    MetadataCache,
    MetadataRequest,
    Term,
    TermPriority,
    WikidataGlossaryProvider,
)

SRT = """1
00:00:01,000 --> 00:00:02,000
Hello
"""

TRANSLATED = """1
00:00:01,000 --> 00:00:02,000
你好
"""


class JsonResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class SeriesIdentity:
    def get_series_wikidata_id(self, series_id: str) -> str | None:
        assert series_id == "1399"
        return "Q123"


class MetadataGlossaryFixture:
    def __init__(self, glossary: Glossary):
        self.glossary = glossary
        self.glossary_calls = 0

    def get_series_overview(self, series_id: str) -> str:
        return "A series overview"

    def get_episode_overview(
        self, series_id: str, season_number: int, episode_number: int
    ) -> str:
        return "An episode overview"

    def get_glossary(self, series_id: str, target_language: str) -> Glossary:
        self.glossary_calls += 1
        assert target_language == "zh"
        return self.glossary


class RetryableGlossaryFixture(MetadataGlossaryFixture):
    def __init__(self, glossary: Glossary):
        super().__init__(glossary)
        self.fail = True

    def get_glossary(self, series_id: str, target_language: str) -> Glossary:
        self.glossary_calls += 1
        if self.fail:
            raise RuntimeError("temporary Wikidata outage")
        return self.glossary


class GlossaryTranslator:
    def __init__(self) -> None:
        self.contexts: list[str] = []
        self.glossaries: list[Glossary] = []

    def translate(
        self,
        source: Path,
        target_language: str,
        *,
        context: str = "",
        glossary: Glossary | None = None,
    ) -> str:
        self.contexts.append(context)
        self.glossaries.append(glossary or Glossary())
        return TRANSLATED


def create_media_and_source(tmp_path):
    media = tmp_path / "Movie.mkv"
    source = tmp_path / "Movie.en.srt"
    media.write_bytes(b"media")
    source.write_text(SRT, encoding="utf-8")
    return media, source


def test_wikidata_terms_are_targeted_and_retain_provenance(monkeypatch):
    requests = []
    response = {
        "results": {
            "bindings": [
                {
                    "entity": {"value": "http://www.wikidata.org/entity/Q1"},
                    "sourceLabel": {"value": "Jon Snow"},
                    "targetLabel": {"value": "琼恩·雪诺"},
                    "rank": {"value": "preferred"},
                },
                {
                    "entity": {"value": "http://www.wikidata.org/entity/Q2"},
                    "sourceLabel": {"value": "The Night's Watch"},
                    "targetLabel": {"value": "守夜人"},
                    "rank": {"value": "normal"},
                },
            ]
        }
    }

    def urlopen(request, *, timeout):
        requests.append((request, timeout))
        return JsonResponse(json.dumps(response).encode("utf-8"))

    monkeypatch.setattr("cueweaver.metadata.urllib.request.urlopen", urlopen)
    glossary = WikidataGlossaryProvider(
        series_identity_provider=SeriesIdentity(),
        sparql_url="https://query.wikidata.test/sparql",
    ).get_glossary("1399", "zh")

    assert glossary.mapping == {
        "Jon Snow": "琼恩·雪诺",
        "The Night's Watch": "守夜人",
    }
    assert glossary.terms[0] == Term(
        source="Jon Snow",
        target="琼恩·雪诺",
        provider="wikidata",
        source_url="https://www.wikidata.org/wiki/Q1",
        entity_id="Q1",
        priority=TermPriority.WIKIDATA_PREFERRED,
    )
    assert glossary.terms[0].entity_id == "Q1"
    assert len(requests) == 1
    assert "Q123" in requests[0][0].full_url
    assert "zh" in requests[0][0].full_url


def test_unresolved_wikidata_term_uses_structured_wikipedia_langlink(
    monkeypatch,
):
    requests = []
    responses = iter(
        [
            {
                "results": {
                    "bindings": [
                        {
                            "entity": {"value": "http://www.wikidata.org/entity/Q9"},
                            "sourceLabel": {"value": "Unknown Hero"},
                        }
                    ]
                }
            },
            {
                "query": {
                    "pages": [
                        {
                            "pageid": 42,
                            "title": "Unknown Hero",
                            "pageprops": {"wikibase_item": "Q9"},
                            "langlinks": [{"lang": "zh", "*": "未知英雄"}],
                        }
                    ]
                }
            },
        ]
    )

    def urlopen(request, *, timeout):
        requests.append(request)
        return JsonResponse(json.dumps(next(responses)).encode("utf-8"))

    monkeypatch.setattr("cueweaver.metadata.urllib.request.urlopen", urlopen)
    glossary = WikidataGlossaryProvider(
        series_identity_provider=SeriesIdentity(),
        sparql_url="https://query.wikidata.test/sparql",
        wikipedia_api_url="https://zh.wikipedia.test/w/api.php",
    ).get_glossary("1399", "zh")

    assert glossary.mapping == {"Unknown Hero": "未知英雄"}
    term = glossary.terms[0]
    assert term.provider == "wikipedia-langlink"
    assert term.source_url == "https://en.wikipedia.org/wiki/Unknown_Hero"
    assert term.entity_id == "Q9"
    assert term.priority is TermPriority.WIKIPEDIA_LANGLINK
    assert len(requests) == 2
    assert "action=query" in requests[1].full_url
    assert "prop=langlinks%7Cpageprops" in requests[1].full_url


def test_wikipedia_fallback_drops_a_same_name_different_wikidata_entity(
    monkeypatch,
):
    responses = iter(
        [
            {
                "results": {
                    "bindings": [
                        {
                            "entity": {"value": "http://www.wikidata.org/entity/Q9"},
                            "sourceLabel": {"value": "Unknown Hero"},
                        }
                    ]
                }
            },
            {
                "query": {
                    "pages": [
                        {
                            "pageid": 42,
                            "title": "Unknown Hero",
                            "pageprops": {"wikibase_item": "Q10"},
                            "langlinks": [{"lang": "zh", "*": "未知英雄"}],
                        }
                    ]
                }
            },
        ]
    )

    def urlopen(_request, *, timeout):
        return JsonResponse(json.dumps(next(responses)).encode("utf-8"))

    monkeypatch.setattr("cueweaver.metadata.urllib.request.urlopen", urlopen)
    glossary = WikidataGlossaryProvider(
        series_identity_provider=SeriesIdentity(),
    ).get_glossary("1399", "zh")

    assert glossary.is_empty


def test_wikipedia_failure_preserves_wikidata_terms(monkeypatch):
    response = {
        "results": {
            "bindings": [
                {
                    "entity": {"value": "http://www.wikidata.org/entity/Q1"},
                    "sourceLabel": {"value": "Known Hero"},
                    "targetLabel": {"value": "已知英雄"},
                },
                {
                    "entity": {"value": "http://www.wikidata.org/entity/Q9"},
                    "sourceLabel": {"value": "Unknown Hero"},
                },
            ]
        }
    }

    def urlopen(request, *, timeout):
        if "query.wikidata.org" in request.full_url:
            return JsonResponse(json.dumps(response).encode("utf-8"))
        raise OSError("Wikipedia is unavailable")

    monkeypatch.setattr("cueweaver.metadata.urllib.request.urlopen", urlopen)
    glossary = WikidataGlossaryProvider(
        series_identity_provider=SeriesIdentity(),
    ).get_glossary("1399", "zh")

    assert glossary.mapping == {"Known Hero": "已知英雄"}


def test_conflicting_metadata_terms_are_dropped_without_inventing_a_mapping(
    monkeypatch,
):
    response = {
        "results": {
            "bindings": [
                {
                    "entity": {"value": "http://www.wikidata.org/entity/Q1"},
                    "sourceLabel": {"value": "The Order"},
                    "targetLabel": {"value": "组织甲"},
                },
                {
                    "entity": {"value": "http://www.wikidata.org/entity/Q2"},
                    "sourceLabel": {"value": "The Order"},
                    "targetLabel": {"value": "组织乙"},
                },
            ]
        }
    }

    def urlopen(request, *, timeout):
        return JsonResponse(json.dumps(response).encode("utf-8"))

    monkeypatch.setattr("cueweaver.metadata.urllib.request.urlopen", urlopen)
    glossary = WikidataGlossaryProvider(
        series_identity_provider=SeriesIdentity(),
    ).get_glossary("1399", "zh")

    assert glossary.terms == ()
    assert glossary.mapping == {}


def test_job_delivers_terms_from_the_structured_provider(tmp_path, monkeypatch):
    media, source = create_media_and_source(tmp_path)
    response = {
        "results": {
            "bindings": [
                {
                    "entity": {"value": "http://www.wikidata.org/entity/Q1"},
                    "sourceLabel": {"value": "Jon Snow"},
                    "targetLabel": {"value": "琼恩·雪诺"},
                }
            ]
        }
    }

    def urlopen(_request, *, timeout):
        return JsonResponse(json.dumps(response).encode("utf-8"))

    monkeypatch.setattr("cueweaver.metadata.urllib.request.urlopen", urlopen)
    translator = GlossaryTranslator()
    result = JobRunner(
        translator=translator,
        metadata_provider=MetadataGlossaryFixture(Glossary()),
        glossary_provider=WikidataGlossaryProvider(
            series_identity_provider=SeriesIdentity(),
        ),
        metadata_cache=MetadataCache(tmp_path / "metadata-cache"),
    ).run(
        media,
        target_language="zh",
        source=source,
        series_id="1399",
        season_number=1,
        episode_number=1,
    )

    assert result.state is JobState.PUBLISHED
    assert result.glossary.mapping == {"Jon Snow": "琼恩·雪诺"}
    assert translator.glossaries == [result.glossary]
    assert result.published_path is not None
    assert result.published_path.read_text(encoding="utf-8") == TRANSLATED


def test_job_delivers_structured_wikipedia_fallback_terms(tmp_path, monkeypatch):
    media, source = create_media_and_source(tmp_path)
    responses = iter(
        [
            {
                "results": {
                    "bindings": [
                        {
                            "entity": {"value": "http://www.wikidata.org/entity/Q9"},
                            "sourceLabel": {"value": "Unknown Hero"},
                        }
                    ]
                }
            },
            {
                "query": {
                    "pages": [
                        {
                            "pageid": 42,
                            "title": "Unknown Hero",
                            "pageprops": {"wikibase_item": "Q9"},
                            "langlinks": [{"lang": "zh", "*": "未知英雄"}],
                        }
                    ]
                }
            },
        ]
    )

    def urlopen(_request, *, timeout):
        return JsonResponse(json.dumps(next(responses)).encode("utf-8"))

    monkeypatch.setattr("cueweaver.metadata.urllib.request.urlopen", urlopen)
    translator = GlossaryTranslator()
    result = JobRunner(
        translator=translator,
        metadata_provider=MetadataGlossaryFixture(Glossary()),
        glossary_provider=WikidataGlossaryProvider(
            series_identity_provider=SeriesIdentity(),
        ),
        metadata_cache=MetadataCache(tmp_path / "metadata-cache"),
    ).run(
        media,
        target_language="zh",
        source=source,
        series_id="1399",
        season_number=1,
        episode_number=1,
    )

    assert result.state is JobState.PUBLISHED
    assert result.glossary.mapping == {"Unknown Hero": "未知英雄"}
    assert result.glossary.terms[0].provider == "wikipedia-langlink"
    assert result.glossary.terms[0].entity_id == "Q9"
    assert translator.glossaries == [result.glossary]
    assert result.published_path is not None
    assert result.published_path.read_text(encoding="utf-8") == TRANSLATED


def test_job_reuses_series_glossary_cache_and_seeds_translation(tmp_path):
    media, source = create_media_and_source(tmp_path)
    glossary = Glossary.from_terms(
        [
            Term(
                source="Jon Snow",
                target="琼恩·雪诺",
                provider="wikidata",
                source_url="https://www.wikidata.org/wiki/Q1",
                entity_id="Q1",
            )
        ]
    )
    metadata = MetadataGlossaryFixture(glossary)
    translator = GlossaryTranslator()
    cache = MetadataCache(tmp_path / "metadata-cache")

    runner = JobRunner(
        translator=translator,
        metadata_provider=metadata,
        metadata_cache=cache,
    )
    first = runner.run(
        media,
        target_language="zh",
        source=source,
        series_id="1399",
        season_number=1,
        episode_number=2,
    )
    second = runner.run(
        media,
        target_language="zh",
        source=source,
        series_id="1399",
        season_number=2,
        episode_number=1,
    )

    assert first.state is JobState.PUBLISHED
    assert second.state is JobState.PUBLISHED
    assert metadata.glossary_calls == 1
    assert translator.glossaries == [glossary, glossary]
    assert first.glossary == glossary
    assert second.glossary == glossary
    assert first.published_path is not None
    assert first.published_path.read_text(encoding="utf-8") == TRANSLATED


def test_series_glossary_cache_keeps_target_language_variants(tmp_path):
    request = MetadataRequest("1399")
    zh = Glossary.from_terms(
        [
            Term(
                source="Jon Snow",
                target="琼恩·雪诺",
                provider="wikidata",
                source_url="https://www.wikidata.org/wiki/Q1",
                entity_id="Q1",
            )
        ]
    )
    ja = Glossary.from_terms(
        [
            Term(
                source="Jon Snow",
                target="ジョン・スノウ",
                provider="wikidata",
                source_url="https://www.wikidata.org/wiki/Q1",
                entity_id="Q1",
            )
        ]
    )
    cache = MetadataCache(tmp_path / "metadata-cache")

    cache.store_glossary(request, zh, target_language="zh")
    cache.store_glossary(request, ja, target_language="ja")

    assert cache.load_glossary(request, "zh") == zh
    assert cache.load_glossary(request, "ja") == ja


def test_empty_series_glossary_publishes_baseline_translation(tmp_path):
    media, source = create_media_and_source(tmp_path)
    metadata = MetadataGlossaryFixture(Glossary())
    translator = GlossaryTranslator()

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
        episode_number=1,
    )

    assert result.state is JobState.PUBLISHED
    assert result.glossary.is_empty
    assert result.metadata_degradation == ("Glossary degraded: no usable series Terms")
    assert translator.glossaries == [Glossary()]
    assert result.published_path is not None
    assert result.published_path.read_text(encoding="utf-8") == TRANSLATED


def test_cancel_during_structured_glossary_gathering_is_terminal(tmp_path, monkeypatch):
    media, source = create_media_and_source(tmp_path)
    request_started = Event()
    release_request = Event()

    def urlopen(_request, *, timeout):
        request_started.set()
        assert release_request.wait(timeout=5)
        return JsonResponse(b'{"results": {"bindings": []}}')

    monkeypatch.setattr("cueweaver.metadata.urllib.request.urlopen", urlopen)
    glossary_provider = WikidataGlossaryProvider(
        series_identity_provider=SeriesIdentity(),
    )
    runner = JobRunner(
        translator=GlossaryTranslator(),
        metadata_provider=MetadataGlossaryFixture(Glossary()),
        glossary_provider=glossary_provider,
        metadata_cache=MetadataCache(tmp_path / "metadata-cache"),
    )
    results = []
    thread = Thread(
        target=lambda: results.append(
            runner.run(
                media,
                target_language="zh",
                source=source,
                series_id="1399",
                season_number=1,
                episode_number=1,
            )
        )
    )
    thread.start()
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


def test_metadata_retry_refreshes_glossary_without_repeating_translation(tmp_path):
    media, source = create_media_and_source(tmp_path)
    glossary = Glossary.from_terms(
        [
            Term(
                source="Jon Snow",
                target="琼恩·雪诺",
                provider="wikidata",
                source_url="https://www.wikidata.org/wiki/Q1",
                entity_id="Q1",
            )
        ]
    )
    metadata = RetryableGlossaryFixture(glossary)
    translator = GlossaryTranslator()
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
        episode_number=1,
    )
    metadata.fail = False
    retried = runner.retry_metadata(first)

    assert first.state is JobState.PUBLISHED
    assert first.metadata_degradation == "Glossary degraded: temporary Wikidata outage"
    assert retried.state is JobState.PUBLISHED
    assert retried.metadata_degradation is None
    assert retried.glossary == glossary
    assert translator.glossaries == [Glossary()]
    assert retried.published_path is not None
    assert retried.published_path.read_text(encoding="utf-8") == TRANSLATED
