import json
from collections.abc import Mapping
from os import PathLike
from pathlib import Path

from cueweaver.overrides import UserOverrideStore


def write_user_override(
    directory: PathLike[str] | str,
    series_scope: str,
    mapping: Mapping[str, object],
) -> tuple[UserOverrideStore, Path]:
    store = UserOverrideStore(directory)
    path = store.path_for(series_scope)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(mapping), encoding="utf-8")
    return store, path
