"""Standalone Model Profile persistence and validation."""

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

# These are supplied by the CueWeaver translation workflow, not by Model Profiles.
MANAGED_SETTINGS = frozenset(
    {
        "provider",
        "provider_settings",
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
        return all(_valid_nested(item) for item in value)
    if isinstance(value, dict):
        return all(
            isinstance(key, str) and _valid_nested(item) for key, item in value.items()
        )
    return False


def _valid_nested(value: object) -> bool:
    return value is None or _valid_json(value)


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

    def require(self, profile_id: str) -> None:
        with self._database.read_session() as session:
            self._require(session, profile_id)

    def resolve(self, profile_id: str) -> dict[str, object]:
        with self._database.read_session() as session:
            row = self._require(session, profile_id)
            settings = self._settings(session, row.id)
            return {
                "provider": row.provider,
                **{setting.key: setting.value for setting in settings},
            }

    def create(
        self, name: object, provider: object, settings: object
    ) -> dict[str, object]:
        return self._save(None, name, provider, settings)

    def replace(
        self, profile_id: str, name: object, provider: object, settings: object
    ) -> dict[str, object]:
        return self._save(profile_id, name, provider, settings)

    def _save(
        self,
        profile_id: str | None,
        name: object,
        provider: object,
        settings: object,
    ) -> dict[str, object]:
        self._validate_input(name, provider, settings)
        assert isinstance(name, str)
        assert isinstance(provider, str)
        assert isinstance(settings, list)
        return self._persist(
            profile_id,
            name,
            provider,
            cast(builtins.list[dict[str, object]], settings),
        )

    @staticmethod
    def _validate_input(name: object, provider: object, settings: object) -> None:
        if not isinstance(name, str) or not name.strip():
            raise ServiceError(
                "invalid_model_profile",
                "Model Profile name must be non-empty",
                field="name",
            )
        if (
            not isinstance(provider, str)
            or provider not in TranslationProvider.get_providers()
        ):
            raise ServiceError(
                "invalid_model_profile_provider",
                "Model Profile provider is not installed",
                field="provider",
            )
        if not isinstance(settings, list):
            raise ServiceError(
                "invalid_model_profile", "Settings must be a list", field="settings"
            )
        seen: set[str] = set()
        for setting in settings:
            if not isinstance(setting, dict) or set(setting) != {"key", "value"}:
                raise ServiceError(
                    "invalid_model_profile",
                    "Setting must contain key and value",
                    field="settings",
                )
            key, value = setting["key"], setting["value"]
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
            if value is None or not _valid_json(value):
                raise ServiceError(
                    "invalid_model_profile",
                    "Setting value must be non-null JSON",
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
        provider: str,
        settings: builtins.list[dict[str, object]],
    ) -> dict[str, object]:
        with self._database.write_transaction(immediate=True) as session:
            row = self._require(session, profile_id) if profile_id is not None else None
            now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            if row is None:
                row = ModelProfileRow(id=uuid.uuid4().hex, created_at=now)
                session.add(row)
            row.name = name
            row.provider = provider
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
                    value=item["value"],
                )
                for item in settings
            )
            session.flush()
            result = self._detail(session, row)
        return result

    def delete(self, profile_id: str) -> dict[str, object]:
        with self._database.write_transaction(immediate=True) as session:
            row = self._require(session, profile_id)
            if not self._can_delete(session, profile_id):
                raise ServiceError(
                    "model_profile_in_use",
                    "Model Profile is used by Jobs and cannot be deleted",
                )
            session.delete(row)
        return {"id": profile_id, "deleted": True}

    @staticmethod
    def _can_delete(session: Session, profile_id: str) -> bool:
        return (
            session.scalar(
                select(JobRow.id).where(JobRow.model_profile_id == profile_id).limit(1)
            )
            is None
        )

    @staticmethod
    def _require(session: Session, profile_id: str) -> ModelProfileRow:
        row = session.get(ModelProfileRow, profile_id)
        if row is None:
            raise ServiceError(
                "model_profile_not_found", "Model Profile does not exist"
            )
        return row

    @staticmethod
    def _settings(
        session: Session, profile_id: str
    ) -> builtins.list[ModelProfileSettingRow]:
        return builtins.list(
            session.scalars(
                select(ModelProfileSettingRow)
                .where(ModelProfileSettingRow.profile_id == profile_id)
                .order_by(ModelProfileSettingRow.key)
            ).all()
        )

    def _detail(self, session: Session, row: ModelProfileRow) -> dict[str, object]:
        return {
            "id": row.id,
            "name": row.name,
            "provider": row.provider,
            "deletable": self._can_delete(session, row.id),
            "created_at": row.created_at,
            "updated_at": row.updated_at,
            "settings": [
                {"key": item.key, "value": item.value}
                for item in self._settings(session, row.id)
            ],
        }
