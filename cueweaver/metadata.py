"""TMDb Context gathering and the long-lived series metadata cache."""

from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from enum import Enum
from os import PathLike
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Any, Protocol

from .publishing import publish_atomically


class MetadataError(Exception):
    """Raised when metadata cannot be gathered."""


class MetadataConfigurationError(MetadataError):
    """Raised when TMDb credentials or identifiers are not configured."""


class MetadataProviderError(MetadataError):
    """Raised when a metadata provider cannot return its response."""


class TermPriority(str, Enum):
    """Deterministic evidence order for automatic Terms."""

    WIKIPEDIA_LANGLINK = "wikipedia-langlink"
    WIKIDATA_PREFERRED = "wikidata-preferred"
    WIKIDATA_NORMAL = "wikidata-normal"


@dataclass(frozen=True)
class Term:
    """One metadata-derived source-to-target mapping."""

    source: str
    target: str
    provider: str
    source_url: str
    entity_id: str
    priority: TermPriority = TermPriority.WIKIDATA_NORMAL

    def __post_init__(self) -> None:
        object.__setattr__(self, "source", self.source.strip())
        object.__setattr__(self, "target", self.target.strip())
        object.__setattr__(self, "provider", self.provider.strip())
        object.__setattr__(self, "source_url", self.source_url.strip())
        object.__setattr__(self, "entity_id", self.entity_id.strip())
        object.__setattr__(self, "priority", TermPriority(self.priority))


@dataclass(frozen=True)
class Glossary:
    """A deterministic, provenance-carrying collection of automatic Terms."""

    terms: tuple[Term, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "terms", tuple(self.terms))

    @classmethod
    def from_terms(cls, terms: list[Term] | tuple[Term, ...]) -> Glossary:
        """Keep only unambiguous mappings, ordered by stable source spelling."""

        priority_order = (
            TermPriority.WIKIPEDIA_LANGLINK,
            TermPriority.WIKIDATA_PREFERRED,
            TermPriority.WIKIDATA_NORMAL,
        )
        candidates: dict[str, list[Term]] = {}
        for term in terms:
            source = term.source.strip()
            target = term.target.strip()
            if (
                not source
                or not target
                or not term.provider
                or not term.source_url
                or not term.entity_id
                or source.casefold() == target.casefold()
            ):
                continue
            candidates.setdefault(source.casefold(), []).append(term)

        selected: list[Term] = []
        for source_terms in candidates.values():
            best_priority = min(
                priority_order.index(term.priority) for term in source_terms
            )
            best_terms = [
                term
                for term in source_terms
                if priority_order.index(term.priority) == best_priority
            ]
            targets = {term.target.casefold() for term in best_terms}
            if len(targets) != 1:
                continue
            selected.append(
                min(
                    best_terms,
                    key=lambda term: (
                        term.source,
                        term.target,
                        term.provider,
                        term.entity_id,
                        term.source_url,
                    ),
                )
            )

        return cls(
            tuple(
                sorted(
                    selected,
                    key=lambda term: (term.source.casefold(), term.source),
                )
            )
        )

    @property
    def mapping(self) -> dict[str, str]:
        """Return the seed mapping accepted by PySubtrans."""

        return {term.source: term.target for term in self.terms}

    @property
    def is_empty(self) -> bool:
        return not self.terms


@dataclass(frozen=True)
class MetadataRequest:
    """Identify one series and, optionally, one episode within it."""

    series_id: str
    season_number: int | None = None
    episode_number: int | None = None
    cache_key: str | None = None
    source_language: str | None = None
    target_language: str | None = None

    def __post_init__(self) -> None:
        series_id = self.series_id.strip()
        if not series_id:
            raise MetadataConfigurationError("TMDb series ID is required")
        object.__setattr__(self, "series_id", series_id)
        if self.cache_key is not None:
            cache_key = self.cache_key.strip()
            if not cache_key:
                raise MetadataConfigurationError("Metadata cache key cannot be empty")
            object.__setattr__(self, "cache_key", cache_key)
        for field_name in ("source_language", "target_language"):
            language = getattr(self, field_name)
            if language is not None:
                language = _metadata_cache_language(language)
                if not language:
                    raise MetadataConfigurationError(
                        f"Metadata {field_name.replace('_', ' ')} cannot be empty"
                    )
                object.__setattr__(self, field_name, language)
        if (self.season_number is None) != (self.episode_number is None):
            raise MetadataConfigurationError(
                "Both season and episode numbers are required for episode Context"
            )
        if self.season_number is not None and self.season_number < 1:
            raise MetadataConfigurationError("Season number must be positive")
        if self.episode_number is not None and self.episode_number < 1:
            raise MetadataConfigurationError("Episode number must be positive")

    @property
    def episode_key(self) -> str | None:
        if self.season_number is None or self.episode_number is None:
            return None
        return f"{self.season_number}x{self.episode_number}"

    @property
    def cache_identity(self) -> str:
        """Return the stable series identity used by the metadata cache."""

        return self.cache_key or self.series_id

    @property
    def language_pair(self) -> str:
        """Return the language-pair cache variant for this request."""

        source = self.source_language or "unknown"
        target = self.target_language or "unknown"
        return f"{source}->{target}"


@dataclass(frozen=True)
class MetadataValue:
    """One localized title and overview returned by a metadata provider."""

    title: str = ""
    overview: str = ""


@dataclass(frozen=True)
class MetadataContext:
    """The complete narrative Context made available to translation."""

    request: MetadataRequest
    series_overview: str = ""
    episode_overview: str = ""
    degradation: str | None = None
    glossary: Glossary = Glossary()
    series_title_source: str = ""
    series_overview_source: str = ""
    series_title_target: str = ""
    series_overview_target: str = ""
    episode_title_source: str = ""
    episode_overview_source: str = ""
    episode_title_target: str = ""
    episode_overview_target: str = ""
    localized: bool = False

    @property
    def text(self) -> str:
        sections: list[str] = []
        source_language = self.request.source_language
        target_language = self.request.target_language
        if self.localized and (
            source_language is not None or target_language is not None
        ):
            self._append_localized_sections(
                sections,
                "Series",
                source_language,
                self.series_title_source,
                self.series_overview_source,
                "source",
            )
            self._append_localized_sections(
                sections,
                "Series",
                target_language,
                self.series_title_target,
                self.series_overview_target,
                "target",
            )
            episode_suffix = self._episode_suffix()
            self._append_localized_sections(
                sections,
                "Episode",
                source_language,
                self.episode_title_source,
                self.episode_overview_source,
                "source",
                episode_suffix,
            )
            self._append_localized_sections(
                sections,
                "Episode",
                target_language,
                self.episode_title_target,
                self.episode_overview_target,
                "target",
                episode_suffix,
            )
        else:
            if self.series_overview:
                sections.append(f"TMDb series overview:\n{self.series_overview}")
            if self.episode_overview:
                episode_label = "TMDb episode overview"
                if (
                    self.request.season_number is not None
                    and self.request.episode_number is not None
                ):
                    episode_label += (
                        f" (S{self.request.season_number:02d}"
                        f"E{self.request.episode_number:02d})"
                    )
                sections.append(f"{episode_label}:\n{self.episode_overview}")

        if not sections:
            return translation_context_instructions()
        return f"{translation_context_instructions()}\n\n---\n\n" + "\n\n".join(
            sections
        )

    def _append_localized_sections(
        self,
        sections: list[str],
        entity: str,
        language: str | None,
        title: str,
        overview: str,
        role: str,
        suffix: str = "",
    ) -> None:
        if language is None:
            return
        if title:
            sections.append(f"{entity} title ({role}: {language}){suffix}:\n{title}")
        if overview:
            sections.append(
                f"{entity} overview ({role}: {language}){suffix}:\n{overview}"
            )

    def _episode_suffix(self) -> str:
        if self.request.season_number is None or self.request.episode_number is None:
            return ""
        return f" (S{self.request.season_number:02d}E{self.request.episode_number:02d})"


_TRANSLATION_CONTEXT_INSTRUCTIONS = """## Translation Context

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
2. Immediate subtitle/dialogue context
3. Source-language episode metadata
4. Source-language series metadata
5. Target-language episode metadata
6. Target-language series metadata"""


def translation_context_instructions() -> str:
    """Return the fixed translation guidance independent of fetched metadata."""

    return _TRANSLATION_CONTEXT_INSTRUCTIONS


class MetadataProvider(Protocol):
    def get_series_title(self, series_id: str, language: str | None = None) -> str:
        """Return the localized TMDb series title."""

    def get_series_overview(self, series_id: str, language: str | None = None) -> str:
        """Return the full TMDb series overview."""

    def get_episode_title(
        self,
        series_id: str,
        season_number: int,
        episode_number: int,
        language: str | None = None,
    ) -> str:
        """Return the localized TMDb episode title."""

    def get_episode_overview(
        self,
        series_id: str,
        season_number: int,
        episode_number: int,
        language: str | None = None,
    ) -> str:
        """Return the full TMDb episode overview."""


class GlossaryProvider(Protocol):
    def get_glossary(self, series_id: str, target_language: str) -> Glossary:
        """Return metadata-derived Terms for one series."""


class SeriesWikidataIdentifierProvider(Protocol):
    def get_series_wikidata_id(self, series_id: str) -> str | None:
        """Return the Wikidata QID associated with one series."""


class MetadataCache:
    """Persist series Context and Glossary data without expiry or polling."""

    def __init__(self, directory: PathLike[str] | str) -> None:
        self.directory = Path(directory).expanduser().resolve()
        self._lock = Lock()

    def load(
        self, request: MetadataRequest
    ) -> tuple[dict[str, MetadataValue], dict[str, MetadataValue]]:
        """Return cached localized series and episode metadata, if present."""

        with self._lock:
            payload = self._read(request.cache_identity)
            variants = payload.get("contexts")
            if isinstance(variants, dict):
                variant = variants.get(request.language_pair)
                if isinstance(variant, dict):
                    series = _metadata_values(variant.get("series"))
                    episodes = variant.get("episodes")
                    episode: dict[str, MetadataValue] = {}
                    if request.episode_key is not None and isinstance(episodes, dict):
                        episode = _metadata_values(episodes.get(request.episode_key))
                    return series, episode

            # Read the pre-language-pair cache format for existing Jobs, but never
            # write new values into that shared namespace.
            legacy_series_value = _metadata_value(
                {"overview": payload.get("series_overview")}
            )
            legacy_series = (
                {"legacy": legacy_series_value}
                if legacy_series_value is not None
                else {}
            )
            legacy_episode: dict[str, MetadataValue] = {}
            episodes = payload.get("episodes")
            if request.episode_key is not None and isinstance(episodes, dict):
                legacy_episode_value = _metadata_value(
                    {"overview": episodes.get(request.episode_key)}
                )
                if legacy_episode_value is not None:
                    legacy_episode["legacy"] = legacy_episode_value
            return legacy_series, legacy_episode

    def store(
        self,
        request: MetadataRequest,
        *,
        language: str | None = None,
        series_title: str | None = None,
        series_overview: str | None = None,
        episode_title: str | None = None,
        episode_overview: str | None = None,
    ) -> None:
        """Merge successful localized provider responses into the series cache."""

        with self._lock:
            payload = self._read(request.cache_identity)
            payload["series_id"] = request.series_id
            if request.cache_key is not None:
                payload["series_qid"] = request.cache_key
            variants = payload.setdefault("contexts", {})
            if not isinstance(variants, dict):
                variants = {}
                payload["contexts"] = variants
            variant = variants.setdefault(request.language_pair, {})
            if not isinstance(variant, dict):
                variant = {}
                variants[request.language_pair] = variant
            series = variant.setdefault("series", {})
            if not isinstance(series, dict):
                series = {}
                variant["series"] = series
            if language is not None:
                language_values = series.setdefault("languages", {})
                if not isinstance(language_values, dict):
                    language_values = {}
                    series["languages"] = language_values
                series = language_values.setdefault(
                    _metadata_cache_language(language), {}
                )
                if not isinstance(series, dict):
                    series = {}
                    language_values[_metadata_cache_language(language)] = series
            if series_title is not None:
                series["title"] = series_title
            if series_overview is not None:
                series["overview"] = series_overview
            episodes = variant.setdefault("episodes", {})
            if not isinstance(episodes, dict):
                episodes = {}
                variant["episodes"] = episodes
            if request.episode_key is not None and (
                episode_title is not None or episode_overview is not None
            ):
                episode = episodes.setdefault(request.episode_key, {})
                if not isinstance(episode, dict):
                    episode = {}
                    episodes[request.episode_key] = episode
                if language is not None:
                    language_values = episode.setdefault("languages", {})
                    if not isinstance(language_values, dict):
                        language_values = {}
                        episode["languages"] = language_values
                    episode = language_values.setdefault(
                        _metadata_cache_language(language), {}
                    )
                    if not isinstance(episode, dict):
                        episode = {}
                        language_values[_metadata_cache_language(language)] = episode
                if episode_title is not None:
                    episode["title"] = episode_title
                if episode_overview is not None:
                    episode["overview"] = episode_overview
            self._write(request.cache_identity, payload)

    def clear_context(self, request: MetadataRequest) -> None:
        """Remove the requested language values before a manual refresh."""

        with self._lock:
            payload = self._read(request.cache_identity)
            variants = payload.get("contexts")
            if not isinstance(variants, dict):
                return
            variant = variants.get(request.language_pair)
            if not isinstance(variant, dict):
                return
            languages = {
                language
                for language in (request.source_language, request.target_language)
                if language is not None
            }
            series = variant.get("series")
            if isinstance(series, dict):
                _clear_metadata_languages(series, languages)
            if request.episode_key is not None:
                episodes = variant.get("episodes")
                if isinstance(episodes, dict):
                    episode = episodes.get(request.episode_key)
                    if isinstance(episode, dict):
                        _clear_metadata_languages(episode, languages)
            self._write(request.cache_identity, payload)

    def load_glossary(
        self,
        request: MetadataRequest,
        target_language: str | None = None,
    ) -> Glossary | None:
        """Return a cached series/Target-language Glossary."""

        with self._lock:
            payload = self._read(request.cache_identity)
            raw_terms: object | None = None
            variants = payload.get("glossaries")
            if target_language is not None and isinstance(variants, dict):
                raw_terms = variants.get(_metadata_cache_language(target_language))
            elif target_language is None:
                raw_terms = payload.get("glossary")
            if not isinstance(raw_terms, list):
                return None
            terms: list[Term] = []
            for raw_term in raw_terms:
                if not isinstance(raw_term, dict):
                    continue
                try:
                    terms.append(
                        Term(
                            source=str(raw_term["source"]),
                            target=str(raw_term["target"]),
                            provider=str(raw_term["provider"]),
                            source_url=str(raw_term["source_url"]),
                            entity_id=str(raw_term["entity_id"]),
                            priority=TermPriority(
                                str(
                                    raw_term.get(
                                        "priority",
                                        TermPriority.WIKIDATA_NORMAL.value,
                                    )
                                )
                            ),
                        )
                    )
                except (KeyError, TypeError, ValueError):
                    continue
            return Glossary.from_terms(terms)

    def store_glossary(
        self,
        request: MetadataRequest,
        glossary: Glossary,
        *,
        target_language: str | None = None,
    ) -> None:
        """Merge a complete series/Target-language Glossary into the cache."""

        with self._lock:
            payload = self._read(request.cache_identity)
            payload["series_id"] = request.series_id
            if request.cache_key is not None:
                payload["series_qid"] = request.cache_key
            serialized = [
                {
                    "source": term.source,
                    "target": term.target,
                    "provider": term.provider,
                    "source_url": term.source_url,
                    "entity_id": term.entity_id,
                    "priority": term.priority.value,
                }
                for term in glossary.terms
            ]
            if target_language is None:
                payload["glossary"] = serialized
            else:
                variants = payload.setdefault("glossaries", {})
                if not isinstance(variants, dict):
                    variants = {}
                    payload["glossaries"] = variants
                variants[_metadata_cache_language(target_language)] = serialized
            self._write(request.cache_identity, payload)

    def _read(self, series_id: str) -> dict[str, object]:
        try:
            payload = json.loads(self._path(series_id).read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError):
            return {}
        if not isinstance(payload, dict):
            return {}
        return payload

    def _write(self, series_id: str, payload: dict[str, object]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        content = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        publish_atomically(content, self._path(series_id))

    def _path(self, series_id: str) -> Path:
        clean_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", series_id.strip()) or "series"
        digest = hashlib.sha256(series_id.encode("utf-8")).hexdigest()[:12]
        return self.directory / f"{clean_id[:80]}-{digest}.json"


class _CancellableJsonProvider:
    def __init__(self, timeout: float) -> None:
        self.timeout = timeout
        self._cancel_requested = Event()
        self._response_lock = Lock()
        self._active_response: object | None = None

    def cancel(self) -> None:
        """Cancel an in-flight request when the active response supports it."""

        self._cancel_requested.set()
        with self._response_lock:
            response = self._active_response
        close = getattr(response, "close", None)
        if callable(close):
            close()

    def reset_for_job(self) -> None:
        """Clear cancellation from a previous terminal Job."""

        self._cancel_requested.clear()

    def _request_json(
        self,
        request: urllib.request.Request,
        *,
        label: str,
        invalid_response_message: str,
    ) -> dict[str, object]:
        if self._cancel_requested.is_set():
            raise MetadataProviderError(f"{label} request canceled")
        outcome: list[tuple[object | None, Exception | None]] = []

        def fetch() -> None:
            if self._cancel_requested.is_set():
                return
            response: Any = None
            try:
                response = urllib.request.urlopen(request, timeout=self.timeout)
                with self._response_lock:
                    self._active_response = response
                    canceled = self._cancel_requested.is_set()
                if canceled:
                    close = getattr(response, "close", None)
                    if callable(close):
                        close()
                with response:
                    outcome.append((json.load(response), None))
            except (OSError, TypeError, ValueError) as error:
                outcome.append((None, error))
            finally:
                with self._response_lock:
                    if self._active_response is response:
                        self._active_response = None

        worker = Thread(target=fetch, daemon=True)
        worker.start()
        while worker.is_alive():
            worker.join(0.05)
            if self._cancel_requested.is_set():
                raise MetadataProviderError(f"{label} request canceled")
        if self._cancel_requested.is_set():
            raise MetadataProviderError(f"{label} request canceled")
        if not outcome:
            raise MetadataProviderError(f"{label} request produced no response")
        payload, error = outcome[0]
        if error is not None:
            raise MetadataProviderError(f"{label} request failed: {error}") from error
        if not isinstance(payload, dict):
            raise MetadataProviderError(invalid_response_message)
        return payload


class TMDbMetadataProvider(_CancellableJsonProvider):
    """Fetch full series and episode overviews from the TMDb v3 API."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        language: str = "en-US",
        base_url: str = "https://api.themoviedb.org/3",
        timeout: float = 30.0,
    ) -> None:
        self.api_key = api_key or os.environ.get(
            "CUEWEAVER_TMDB_API_KEY", os.environ.get("TMDB_API_KEY")
        )
        self.language = language
        self.base_url = base_url.rstrip("/")
        super().__init__(timeout)

    def get_series_title(self, series_id: str, language: str | None = None) -> str:
        payload = self._get(
            f"tv/{urllib.parse.quote(series_id, safe='')}", language=language
        )
        return _title(payload, "series")

    def get_series_overview(self, series_id: str, language: str | None = None) -> str:
        payload = self._get(
            f"tv/{urllib.parse.quote(series_id, safe='')}", language=language
        )
        return _overview(payload, "series")

    def get_series_wikidata_id(self, series_id: str) -> str | None:
        payload = self._get(f"tv/{urllib.parse.quote(series_id, safe='')}/external_ids")
        value = payload.get("wikidata_id")
        if not isinstance(value, str):
            return None
        value = value.strip()
        return value if re.fullmatch(r"Q[1-9][0-9]*", value) else None

    def get_episode_overview(
        self,
        series_id: str,
        season_number: int,
        episode_number: int,
        language: str | None = None,
    ) -> str:
        payload = self._get(
            "tv/"
            f"{urllib.parse.quote(series_id, safe='')}/season/{season_number}"
            f"/episode/{episode_number}",
            language=language,
        )
        return _overview(payload, "episode")

    def get_episode_title(
        self,
        series_id: str,
        season_number: int,
        episode_number: int,
        language: str | None = None,
    ) -> str:
        payload = self._get(
            "tv/"
            f"{urllib.parse.quote(series_id, safe='')}/season/{season_number}"
            f"/episode/{episode_number}",
            language=language,
        )
        return _title(payload, "episode")

    def _get(self, path: str, *, language: str | None = None) -> dict[str, object]:
        if not self.api_key:
            raise MetadataConfigurationError(
                "TMDb API key is missing; set CUEWEAVER_TMDB_API_KEY or TMDB_API_KEY"
            )
        if self._cancel_requested.is_set():
            raise MetadataProviderError("TMDb request canceled")
        query = urllib.parse.urlencode({"language": language or self.language})
        headers = {"User-Agent": "CueWeaver/0.1"}
        if self.api_key.count(".") == 2:
            headers["Authorization"] = f"Bearer {self.api_key}"
        else:
            query = f"{query}&{urllib.parse.urlencode({'api_key': self.api_key})}"
        request = urllib.request.Request(
            f"{self.base_url}/{path}?{query}", headers=headers
        )
        return self._request_json(
            request,
            label="TMDb",
            invalid_response_message="TMDb returned an invalid metadata response",
        )


def _overview(payload: dict[str, object], label: str) -> str:
    overview = payload.get("overview", "")
    if not isinstance(overview, str):
        raise MetadataProviderError(f"TMDb returned an invalid {label} overview")
    return overview


def _title(payload: dict[str, object], label: str) -> str:
    title = payload.get("name", "")
    if not isinstance(title, str):
        raise MetadataProviderError(f"TMDb returned an invalid {label} title")
    return title


def _metadata_value(value: object) -> MetadataValue | None:
    if not isinstance(value, dict):
        return None
    title = value.get("title", "")
    overview = value.get("overview", "")
    if not isinstance(title, str) or not isinstance(overview, str):
        return None
    if not title and not overview:
        return None
    return MetadataValue(title=title, overview=overview)


def _metadata_values(value: object) -> dict[str, MetadataValue]:
    if not isinstance(value, dict):
        return {}
    languages = value.get("languages")
    if isinstance(languages, dict):
        return {
            str(language): metadata
            for language, raw_value in languages.items()
            if (metadata := _metadata_value(raw_value)) is not None
        }
    metadata = _metadata_value(value)
    return {"legacy": metadata} if metadata is not None else {}


def _clear_metadata_languages(value: dict[str, object], languages: set[str]) -> None:
    raw_languages = value.get("languages")
    if not isinstance(raw_languages, dict):
        value.pop("title", None)
        value.pop("overview", None)
        return
    for language in languages:
        raw_languages.pop(_metadata_cache_language(language), None)


class WikidataGlossaryProvider(_CancellableJsonProvider):
    """Build a series Glossary from structured Wikidata and Wikipedia evidence."""

    _TERM_CLASSES = (
        "Q95074",  # fictional character
        "Q43229",  # organization
        "Q618123",  # geographic feature
        "Q2221906",  # fictional location
        "Q16521",  # taxon/species
        "Q55983715",  # fictional species
    )

    def __init__(
        self,
        *,
        series_identity_provider: SeriesWikidataIdentifierProvider | None = None,
        sparql_url: str = "https://query.wikidata.org/sparql",
        wikipedia_api_url: str = "https://en.wikipedia.org/w/api.php",
        timeout: float = 30.0,
    ) -> None:
        self.series_identity_provider = series_identity_provider
        self.sparql_url = sparql_url
        self.wikipedia_api_url = wikipedia_api_url
        super().__init__(timeout)

    def get_glossary(self, series_id: str, target_language: str) -> Glossary:
        series_qid = self._series_qid(series_id)
        if series_qid is None:
            return Glossary()

        target_code = _metadata_language_code(target_language)
        payload = self._get_json(
            self.sparql_url,
            {
                "query": _wikidata_query(series_qid, target_code),
                "format": "json",
            },
        )
        rows = _sparql_bindings(payload)
        wikidata_terms: list[Term] = []
        unresolved_labels: dict[str, tuple[str, str]] = {}
        ambiguous_labels: set[str] = set()
        candidates: dict[str, list[Term]] = {}

        for row in rows:
            source = _binding_text(
                row,
                "sourceLabel",
                "entityLabel",
                "source_label",
            )
            if source is None:
                continue
            source_key = source.casefold()
            target = _binding_text(
                row,
                "targetLabel",
                "entityTargetLabel",
                "target_label",
            )
            entity_id = _entity_id_from_row(row)
            if entity_id is None:
                continue
            if target is None:
                previous = unresolved_labels.get(source_key)
                if previous is not None and previous[1] != entity_id:
                    ambiguous_labels.add(source_key)
                else:
                    unresolved_labels[source_key] = (source, entity_id)
                continue
            term = Term(
                source=source,
                target=target,
                provider="wikidata",
                source_url=f"https://www.wikidata.org/wiki/{entity_id}",
                entity_id=entity_id,
                priority=_wikidata_priority(row),
            )
            candidates.setdefault(source_key, []).append(term)

        for source_key, source_terms in candidates.items():
            best_priority = _best_priority(source_terms)
            best_terms = [
                term for term in source_terms if term.priority is best_priority
            ]
            if len({term.target.casefold() for term in best_terms}) != 1:
                ambiguous_labels.add(source_key)
            else:
                wikidata_terms.extend(source_terms)

        wikidata_glossary = Glossary.from_terms(wikidata_terms)
        resolved = {term.source.casefold() for term in wikidata_glossary.terms}
        fallback_terms: list[Term] = []
        for source_key in sorted(unresolved_labels):
            if source_key in resolved or source_key in ambiguous_labels:
                continue
            source, entity_id = unresolved_labels[source_key]
            try:
                fallback = self._wikipedia_langlink(
                    source,
                    target_code,
                    expected_entity_id=entity_id,
                )
            except MetadataProviderError:
                break
            if fallback is not None:
                fallback_terms.append(fallback)

        return Glossary.from_terms([*wikidata_glossary.terms, *fallback_terms])

    def _series_qid(self, series_id: str) -> str | None:
        if self.series_identity_provider is not None:
            value = self.series_identity_provider.get_series_wikidata_id(series_id)
            if value is None:
                return None
            return value if re.fullmatch(r"Q[1-9][0-9]*", value) else None
        return series_id if re.fullmatch(r"Q[1-9][0-9]*", series_id) else None

    def _wikipedia_langlink(
        self,
        source: str,
        target_code: str,
        *,
        expected_entity_id: str,
    ) -> Term | None:
        payload = self._get_json(
            self.wikipedia_api_url,
            {
                "action": "query",
                "format": "json",
                "formatversion": "2",
                "prop": "langlinks|pageprops",
                "titles": source,
                "redirects": "1",
                "lllang": target_code,
                "lllimit": "10",
            },
        )
        pages = _wikipedia_pages(payload)
        if len(pages) != 1:
            return None
        page = pages[0]
        pageprops = page.get("pageprops")
        if not isinstance(pageprops, dict) or "disambiguation" in pageprops:
            return None
        page_title = page.get("title")
        if not isinstance(page_title, str) or not page_title.strip():
            return None
        raw_links = page.get("langlinks")
        if not isinstance(raw_links, list):
            return None
        links = [
            link
            for link in raw_links
            if isinstance(link, dict)
            and str(link.get("lang", "")).casefold() == target_code
        ]
        if len(links) != 1:
            return None
        target = links[0].get("*", links[0].get("title"))
        if not isinstance(target, str) or not target.strip():
            return None
        target = target.strip()
        if source.casefold() == target.casefold():
            return None

        entity_id = pageprops.get("wikibase_item")
        if not isinstance(entity_id, str) or entity_id.strip() != expected_entity_id:
            return None
        source_url = "https://en.wikipedia.org/wiki/" + urllib.parse.quote(
            page_title.strip().replace(" ", "_"), safe="()/:,"
        )
        return Term(
            source=source,
            target=target,
            provider="wikipedia-langlink",
            source_url=source_url,
            entity_id=entity_id,
            priority=TermPriority.WIKIPEDIA_LANGLINK,
        )

    def _get_json(
        self,
        base_url: str,
        params: dict[str, str],
    ) -> dict[str, object]:
        if self._cancel_requested.is_set():
            raise MetadataProviderError("Structured Glossary metadata request canceled")
        query = urllib.parse.urlencode(params)
        request = urllib.request.Request(
            f"{base_url}?{query}",
            headers={"User-Agent": "CueWeaver/0.1"},
        )
        return self._request_json(
            request,
            label="Structured Glossary metadata",
            invalid_response_message=(
                "Structured Glossary metadata returned an invalid response"
            ),
        )


def _metadata_language_code(language: str) -> str:
    value = language.strip().casefold().replace("_", "-")
    return value.split("-", 1)[0]


def _metadata_cache_language(language: str) -> str:
    return language.strip().casefold().replace("_", "-")


def _wikidata_query(series_qid: str, target_code: str) -> str:
    class_values = " ".join(
        f"wd:{value}" for value in WikidataGlossaryProvider._TERM_CLASSES
    )
    return f"""
PREFIX p: <http://www.wikidata.org/prop/>
PREFIX ps: <http://www.wikidata.org/prop/statement/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX wd: <http://www.wikidata.org/entity/>
PREFIX wdt: <http://www.wikidata.org/prop/direct/>
PREFIX wikibase: <http://wikiba.se/ontology#>

SELECT DISTINCT ?entity ?sourceLabel ?targetLabel ?rank WHERE {{
  {{
    ?statement ps:P1441 wd:{series_qid} ; wikibase:rank ?rank .
    ?entity p:P1441 ?statement .
  }} UNION {{
    ?statement ps:P674 ?entity ; wikibase:rank ?rank .
    wd:{series_qid} p:P674 ?statement .
  }} UNION {{
    wd:{series_qid} wdt:P527 ?entity .
    BIND("normal" AS ?rank)
  }}
  ?entity wdt:P31/wdt:P279* ?termClass .
  VALUES ?termClass {{ {class_values} }}
  ?entity rdfs:label ?sourceLabel .
  FILTER(LANG(?sourceLabel) = "en")
  OPTIONAL {{
    ?entity rdfs:label ?targetLabel .
    FILTER(LANG(?targetLabel) = "{target_code}")
  }}
}}
""".strip()


def _sparql_bindings(payload: dict[str, object]) -> list[dict[str, object]]:
    results = payload.get("results")
    if not isinstance(results, dict):
        return []
    bindings = results.get("bindings")
    if not isinstance(bindings, list):
        return []
    return [row for row in bindings if isinstance(row, dict)]


def _binding_text(row: dict[str, object], *names: str) -> str | None:
    for name in names:
        value = row.get(name)
        if not isinstance(value, dict):
            continue
        text = value.get("value")
        if isinstance(text, str) and text.strip():
            return text.strip()
    return None


def _entity_id_from_row(row: dict[str, object]) -> str | None:
    value = _binding_text(row, "entity", "entityId", "entity_id")
    if value is None:
        return None
    match = re.search(r"/(Q[1-9][0-9]*)$", value)
    if match is not None:
        return match.group(1)
    return value if re.fullmatch(r"Q[1-9][0-9]*", value) else None


def _wikidata_priority(row: dict[str, object]) -> TermPriority:
    rank = (_binding_text(row, "rank") or "").casefold()
    if rank.endswith("preferredrank") or rank == "preferred":
        return TermPriority.WIKIDATA_PREFERRED
    return TermPriority.WIKIDATA_NORMAL


def _best_priority(terms: list[Term]) -> TermPriority:
    for priority in (
        TermPriority.WIKIPEDIA_LANGLINK,
        TermPriority.WIKIDATA_PREFERRED,
        TermPriority.WIKIDATA_NORMAL,
    ):
        if any(term.priority is priority for term in terms):
            return priority
    return TermPriority.WIKIDATA_NORMAL


def _wikipedia_pages(payload: dict[str, object]) -> list[dict[str, object]]:
    query = payload.get("query")
    if not isinstance(query, dict):
        return []
    raw_pages = query.get("pages")
    if isinstance(raw_pages, list):
        return [page for page in raw_pages if isinstance(page, dict)]
    if isinstance(raw_pages, dict):
        return [page for page in raw_pages.values() if isinstance(page, dict)]
    return []
