"""Model Profile persistence, validation, and single-chain resolution."""

from __future__ import annotations

import builtins
import json
import math
import uuid
from datetime import datetime, timezone
from typing import cast

from PySubtrans.TranslationProvider import TranslationProvider
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .database import JobRow, ModelProfileRow, ModelProfileSettingRow, SqliteDatabase
from .errors import ServiceError

# These are supplied by the CueWeaver translation workflow, not by PySubtrans profiles.
MANAGED_SETTINGS = frozenset(
    {
        "target_language",
        "prompt",
        "preprocess_subtitles",
        "postprocess_translation",
        "build_terminology_map",
        "stop_on_error",
        "project_file",
    }
)


def _valid_json(value: object) -> bool:
    if isinstance(value, (str, bool, int)):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, list):
        return all(isinstance(item, str) for item in value)
    if isinstance(value, dict):
        return all(
            isinstance(key, str) and _valid_nested(item) for key, item in value.items()
        )
    return False


def _valid_nested(value: object) -> bool:
    if value is None:
        return True
    return _valid_json(value)


class ModelProfiles:
    def __init__(self, database: SqliteDatabase) -> None:
        self._database = database

    def list(self) -> list[dict[str, object]]:
        with self._database.read_session() as session:
            rows = session.scalars(
                select(ModelProfileRow).order_by(
                    ModelProfileRow.created_at, ModelProfileRow.id
                )
            ).all()
            return [self._detail(session, row) for row in rows]

    def get(self, profile_id: str) -> dict[str, object]:
        with self._database.read_session() as session:
            return self._detail(session, self._require(session, profile_id))

    def require_selectable(self, profile_id: str) -> None:
        with self._database.read_session() as session:
            row = self._require(session, profile_id)
            if not row.selectable:
                raise ServiceError(
                    "model_profile_not_selectable",
                    "Model Profile cannot be selected for new Jobs",
                )

    def resolve(self, profile_id: str) -> dict[str, object]:
        with self._database.read_session() as session:
            return {
                key: item["value"]
                for key, item in self._effective(
                    session, self._require(session, profile_id)
                ).items()
            }

    def create(
        self, name: object, parent_id: object, selectable: object, settings: object
    ) -> dict[str, object]:
        return self._save(None, name, parent_id, selectable, settings)

    def replace(
        self,
        profile_id: str,
        name: object,
        parent_id: object,
        selectable: object,
        settings: object,
    ) -> dict[str, object]:
        return self._save(profile_id, name, parent_id, selectable, settings)

    def _save(
        self,
        profile_id: str | None,
        name: object,
        parent_id: object,
        selectable: object,
        settings: object,
    ) -> dict[str, object]:
        self._validate_input(name, parent_id, selectable, settings)
        assert isinstance(name, str) and isinstance(settings, list)
        assert isinstance(selectable, bool)
        assert parent_id is None or isinstance(parent_id, str)
        return self._persist(
            profile_id,
            name,
            parent_id,
            selectable,
            cast(builtins.list[dict[str, object]], settings),
        )

    @staticmethod
    def _validate_input(
        name: object, parent_id: object, selectable: object, settings: object
    ) -> None:
        if not isinstance(name, str) or not name.strip():
            raise ServiceError(
                "invalid_model_profile",
                "Model Profile name must be non-empty",
                field="name",
            )
        if parent_id is not None and (not isinstance(parent_id, str) or not parent_id):
            raise ServiceError(
                "invalid_model_profile",
                "Parent Model Profile ID is invalid",
                field="parent_id",
            )
        if not isinstance(selectable, bool):
            raise ServiceError(
                "invalid_model_profile",
                "Selectable must be a boolean",
                field="selectable",
            )
        if not isinstance(settings, list):
            raise ServiceError(
                "invalid_model_profile", "Settings must be a list", field="settings"
            )
        seen: set[str] = set()
        for setting in settings:
            if not isinstance(setting, dict) or set(setting) != {
                "key",
                "kind",
                "value",
            }:
                raise ServiceError(
                    "invalid_model_profile",
                    "Setting must contain key, kind, and value",
                    field="settings",
                )
            key, kind, value = setting["key"], setting["kind"], setting["value"]
            if (
                not isinstance(key, str)
                or not key.strip()
                or key in seen
                or key in MANAGED_SETTINGS
            ):
                raise ServiceError(
                    "invalid_model_profile",
                    "Setting key is duplicate, empty, or managed by CueWeaver",
                    field="settings",
                )
            seen.add(key)
            if not (
                (kind == "unset" and value is None)
                or (kind == "literal" and value is not None and _valid_json(value))
            ):
                raise ServiceError(
                    "invalid_model_profile",
                    "Setting kind or value is invalid",
                    field="settings",
                )
            try:
                json.dumps(value, allow_nan=False)
            except (TypeError, ValueError) as error:
                raise ServiceError(
                    "invalid_model_profile",
                    "Setting value is not valid JSON",
                    field="settings",
                ) from error

    def _persist(
        self,
        profile_id: str | None,
        name: str,
        parent_id: str | None,
        selectable: bool,
        settings: builtins.list[dict[str, object]],
    ) -> dict[str, object]:
        with self._database.write_transaction(immediate=True) as session:
            row = self._require(session, profile_id) if profile_id is not None else None
            if parent_id is not None:
                self._require(session, parent_id)
                if parent_id == profile_id:
                    raise ServiceError(
                        "model_profile_cycle", "A Model Profile cannot parent itself"
                    )
                ancestor: str | None = parent_id
                visited: set[str] = set()
                while ancestor is not None:
                    if ancestor == profile_id or ancestor in visited:
                        raise ServiceError(
                            "model_profile_cycle",
                            "Model Profile inheritance cannot contain a cycle",
                        )
                    visited.add(ancestor)
                    ancestor = self._require(session, ancestor).parent_id
            now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            if row is None:
                row = ModelProfileRow(id=uuid.uuid4().hex, created_at=now)
                session.add(row)
            row.name = name
            row.parent_id = parent_id
            row.selectable = selectable
            row.updated_at = now
            session.flush()
            session.execute(
                delete(ModelProfileSettingRow).where(
                    ModelProfileSettingRow.profile_id == row.id
                )
            )
            session.add_all(
                ModelProfileSettingRow(
                    profile_id=row.id,
                    key=str(item["key"]),
                    kind=str(item["kind"]),
                    value=item["value"],
                )
                for item in settings
            )
            session.flush()
            # Child edges still identify affected descendants after reparenting.
            frontier = [row]
            seen = {row.id}
            while frontier:
                for candidate in frontier:
                    if candidate.selectable:
                        self._validate_provider(self._effective(session, candidate))
                children = session.scalars(
                    select(ModelProfileRow).where(
                        ModelProfileRow.parent_id.in_(
                            candidate.id for candidate in frontier
                        )
                    )
                ).all()
                frontier = [child for child in children if child.id not in seen]
                seen.update(child.id for child in frontier)
            result = self._detail(session, row)
        return result

    def delete(self, profile_id: str) -> dict[str, object]:
        with self._database.write_transaction(immediate=True) as session:
            row = self._require(session, profile_id)
            if not self._can_delete(session, profile_id):
                raise ServiceError(
                    "model_profile_in_use",
                    "Model Profile has children or Jobs and cannot be deleted",
                )
            session.delete(row)
        return {"id": profile_id, "deleted": True}

    @staticmethod
    def _can_delete(session: Session, profile_id: str) -> bool:
        has_child = session.scalar(
            select(ModelProfileRow.id)
            .where(ModelProfileRow.parent_id == profile_id)
            .limit(1)
        )
        has_job = session.scalar(
            select(JobRow.id).where(JobRow.model_profile_id == profile_id).limit(1)
        )
        return has_child is None and has_job is None

    @staticmethod
    def _require(session: Session, profile_id: str) -> ModelProfileRow:
        row = session.get(ModelProfileRow, profile_id)
        if row is None:
            raise ServiceError(
                "model_profile_not_found", "Model Profile does not exist"
            )
        return row

    def _effective(
        self, session: Session, row: ModelProfileRow
    ) -> dict[str, dict[str, object]]:
        chain: list[ModelProfileRow] = []
        seen: set[str] = set()
        current: ModelProfileRow | None = row
        while current is not None:
            if current.id in seen:
                raise ServiceError(
                    "model_profile_cycle",
                    "Model Profile inheritance cannot contain a cycle",
                )
            seen.add(current.id)
            chain.append(current)
            current = (
                self._require(session, current.parent_id) if current.parent_id else None
            )
        effective: dict[str, dict[str, object]] = {}
        for ancestor in reversed(chain):
            for setting in session.scalars(
                select(ModelProfileSettingRow).where(
                    ModelProfileSettingRow.profile_id == ancestor.id
                )
            ):
                if setting.kind == "unset":
                    effective.pop(setting.key, None)
                else:
                    effective[setting.key] = {
                        "key": setting.key,
                        "value": setting.value,
                        "source": {"id": ancestor.id, "name": ancestor.name},
                    }
        return effective

    @staticmethod
    def _validate_provider(effective: dict[str, dict[str, object]]) -> None:
        provider = effective.get("provider", {}).get("value")
        if (
            not isinstance(provider, str)
            or provider not in TranslationProvider.get_providers()
        ):
            raise ServiceError(
                "invalid_model_profile_provider",
                "Selectable Model Profile requires a known provider",
                field="settings",
            )

    def _detail(self, session: Session, row: ModelProfileRow) -> dict[str, object]:
        settings = session.scalars(
            select(ModelProfileSettingRow)
            .where(ModelProfileSettingRow.profile_id == row.id)
            .order_by(ModelProfileSettingRow.key)
        ).all()
        return {
            "id": row.id,
            "name": row.name,
            "parent_id": row.parent_id,
            "selectable": row.selectable,
            "deletable": self._can_delete(session, row.id),
            "created_at": row.created_at,
            "updated_at": row.updated_at,
            "settings": [
                {"key": item.key, "kind": item.kind, "value": item.value}
                for item in settings
            ],
            "effective_settings": list(self._effective(session, row).values()),
        }
