"""Series-scoped, file-based User override loading."""

from __future__ import annotations

import hashlib
import json
import re
from os import PathLike
from pathlib import Path


class UserOverrideError(ValueError):
    """Raised when a User override file cannot be used."""


class UserOverrideStore:
    """Load one JSON Source-to-Target mapping for each series scope.

    A missing optional scope file means that the scope has no User overrides;
    callers can require the file when the override directory is explicitly
    configured. Existing files must contain a JSON object whose keys and values
    are non-empty strings.
    """

    def __init__(self, directory: PathLike[str] | str) -> None:
        self.directory = Path(directory).expanduser().resolve()

    def path_for(self, series_scope: str) -> Path:
        """Return the conventional override path for *series_scope*."""

        raw_scope = series_scope.strip()
        clean_scope = re.sub(r"[^A-Za-z0-9_. -]+", "_", raw_scope)
        if not clean_scope:
            raise UserOverrideError("User override scope must not be empty")
        suffix = ""
        if clean_scope != raw_scope or len(raw_scope) > 80:
            digest = hashlib.sha256(raw_scope.encode("utf-8")).hexdigest()[:12]
            suffix = f"-{digest}"
        return self.directory / f"{clean_scope[:80]}{suffix}.json"

    def load(self, series_scope: str, *, required: bool = False) -> dict[str, str]:
        """Load and validate the override mapping for *series_scope*."""

        path = self.path_for(series_scope)
        if not path.exists():
            if required:
                raise UserOverrideError(f"User override file is missing: {path}")
            return {}
        if not path.is_file():
            raise UserOverrideError(f"User override path is not a file: {path}")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError) as error:
            raise UserOverrideError(
                f"User override file is not valid JSON: {path}"
            ) from error
        if not isinstance(payload, dict):
            raise UserOverrideError(
                f"User override file must contain a JSON object: {path}"
            )

        overrides: dict[str, str] = {}
        seen_sources: dict[str, str] = {}
        for source, target in payload.items():
            if not isinstance(source, str) or not isinstance(target, str):
                raise UserOverrideError(
                    "User override terms must map string Sources to string Targets "
                    f"in {path}"
                )
            source = source.strip()
            target = target.strip()
            if not source or not target:
                raise UserOverrideError(
                    f"User override Sources and Targets must not be empty in {path}"
                )
            source_key = source.casefold()
            previous_source = seen_sources.get(source_key)
            if previous_source is not None:
                raise UserOverrideError(
                    "User override file contains duplicate Source terms ignoring "
                    f"case: {previous_source!r} and {source!r} in {path}"
                )
            seen_sources[source_key] = source
            overrides[source] = target
        return dict(
            sorted(overrides.items(), key=lambda item: (item[0].casefold(), item[0]))
        )
