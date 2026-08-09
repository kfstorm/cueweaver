"""TMDb Context gathering and the long-lived series metadata cache."""

from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
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


@dataclass(frozen=True)
class MetadataRequest:
    """Identify one series and, optionally, one episode within it."""

    series_id: str
    season_number: int | None = None
    episode_number: int | None = None

    def __post_init__(self) -> None:
        series_id = self.series_id.strip()
        if not series_id:
            raise MetadataConfigurationError("TMDb series ID is required")
        object.__setattr__(self, "series_id", series_id)
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


@dataclass(frozen=True)
class MetadataContext:
    """The complete narrative Context made available to translation."""

    request: MetadataRequest
    series_overview: str = ""
    episode_overview: str = ""
    degradation: str | None = None

    @property
    def text(self) -> str:
        sections: list[str] = []
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
        return "\n\n".join(sections)


class MetadataProvider(Protocol):
    def get_series_overview(self, series_id: str) -> str:
        """Return the full TMDb series overview."""

    def get_episode_overview(
        self, series_id: str, season_number: int, episode_number: int
    ) -> str:
        """Return the full TMDb episode overview."""


class MetadataCache:
    """Persist Context by series ID without expiry or eager refresh."""

    def __init__(self, directory: PathLike[str] | str) -> None:
        self.directory = Path(directory).expanduser().resolve()
        self._lock = Lock()

    def load(self, request: MetadataRequest) -> tuple[str | None, str | None]:
        """Return cached series and episode overviews, if present."""

        with self._lock:
            payload = self._read(request.series_id)
            series_overview = payload.get("series_overview")
            episodes = payload.get("episodes")
            episode_overview = None
            if request.episode_key is not None and isinstance(episodes, dict):
                value = episodes.get(request.episode_key)
                if isinstance(value, str):
                    episode_overview = value
            return (
                series_overview if isinstance(series_overview, str) else None,
                episode_overview,
            )

    def store(
        self,
        request: MetadataRequest,
        *,
        series_overview: str | None = None,
        episode_overview: str | None = None,
    ) -> None:
        """Merge successful provider responses into the series cache."""

        with self._lock:
            payload = self._read(request.series_id)
            payload["series_id"] = request.series_id
            if series_overview is not None:
                payload["series_overview"] = series_overview
            episodes = payload.setdefault("episodes", {})
            if not isinstance(episodes, dict):
                episodes = {}
                payload["episodes"] = episodes
            if request.episode_key is not None and episode_overview is not None:
                episodes[request.episode_key] = episode_overview
            self._write(request.series_id, payload)

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


class TMDbMetadataProvider:
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
        self.timeout = timeout
        self._cancel_requested = Event()
        self._response_lock = Lock()
        self._active_response: object | None = None

    def cancel(self) -> None:
        """Mark an in-flight provider operation canceled when possible."""

        self._cancel_requested.set()
        with self._response_lock:
            response = self._active_response
        close = getattr(response, "close", None)
        if callable(close):
            close()

    def reset_for_job(self) -> None:
        """Clear cancellation from a previous terminal Job."""

        self._cancel_requested.clear()

    def get_series_overview(self, series_id: str) -> str:
        payload = self._get(f"tv/{urllib.parse.quote(series_id, safe='')}")
        return _overview(payload, "series")

    def get_episode_overview(
        self, series_id: str, season_number: int, episode_number: int
    ) -> str:
        payload = self._get(
            "tv/"
            f"{urllib.parse.quote(series_id, safe='')}/season/{season_number}"
            f"/episode/{episode_number}"
        )
        return _overview(payload, "episode")

    def _get(self, path: str) -> dict[str, object]:
        if not self.api_key:
            raise MetadataConfigurationError(
                "TMDb API key is missing; set CUEWEAVER_TMDB_API_KEY or TMDB_API_KEY"
            )
        if self._cancel_requested.is_set():
            raise MetadataProviderError("TMDb request canceled")
        query = urllib.parse.urlencode({"language": self.language})
        headers = {"User-Agent": "CueWeaver/0.1"}
        if self.api_key.count(".") == 2:
            headers["Authorization"] = f"Bearer {self.api_key}"
        else:
            query = f"{query}&{urllib.parse.urlencode({'api_key': self.api_key})}"
        request = urllib.request.Request(
            f"{self.base_url}/{path}?{query}", headers=headers
        )
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
                raise MetadataProviderError("TMDb request canceled")
        if self._cancel_requested.is_set():
            raise MetadataProviderError("TMDb request canceled")
        if not outcome:
            raise MetadataProviderError("TMDb request produced no response")
        payload, error = outcome[0]
        if error is not None:
            raise MetadataProviderError(f"TMDb request failed: {error}") from error
        if not isinstance(payload, dict):
            raise MetadataProviderError("TMDb returned an invalid metadata response")
        return payload


def _overview(payload: dict[str, object], label: str) -> str:
    overview = payload.get("overview", "")
    if not isinstance(overview, str):
        raise MetadataProviderError(f"TMDb returned an invalid {label} overview")
    return overview
