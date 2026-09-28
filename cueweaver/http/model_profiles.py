"""HTTP CRUD and runtime options for Model Profiles."""

from typing import Any, Protocol

from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict

from ..adapters.pysubtrans_model_profile_reference import (
    pysubtrans_model_profile_options,
    pysubtrans_model_profile_reference,
)
from ..application.model_profiles import ModelProfiles


class ProfileBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    provider: str
    settings: list[dict[str, Any]]


class ProfileOptionsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str
    settings: dict[str, Any]


class ProfileApplication(Protocol):
    @property
    def model_profiles(self) -> ModelProfiles: ...


def register_model_profiles(app: FastAPI, application: ProfileApplication) -> None:
    @app.get("/api/model-profile-reference")
    def model_profile_reference() -> dict[str, object]:
        return pysubtrans_model_profile_reference()

    @app.post("/api/model-profile-options")
    def model_profile_options(body: ProfileOptionsBody) -> dict[str, object]:
        return pysubtrans_model_profile_options(body.provider, body.settings)

    @app.get("/api/model-profiles")
    def list_profiles() -> dict[str, object]:
        return {"model_profiles": application.model_profiles.list()}

    @app.post("/api/model-profiles")
    def create_profile(body: ProfileBody) -> dict[str, object]:
        return application.model_profiles.create(
            body.name, body.provider, body.settings
        )

    @app.get("/api/model-profiles/{profile_id}")
    def get_profile(profile_id: str) -> dict[str, object]:
        return application.model_profiles.get(profile_id)

    @app.put("/api/model-profiles/{profile_id}")
    def replace_profile(profile_id: str, body: ProfileBody) -> dict[str, object]:
        return application.model_profiles.replace(
            profile_id, body.name, body.provider, body.settings
        )

    @app.delete("/api/model-profiles/{profile_id}")
    def delete_profile(profile_id: str) -> dict[str, object]:
        return application.model_profiles.delete(profile_id)
