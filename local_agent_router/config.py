from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, HttpUrl, field_validator


class Capabilities(BaseModel):
    text: bool = True
    vision: bool = False
    tool_calling: bool = False
    max_context: int = 8192


class HealthCheck(BaseModel):
    url: HttpUrl | None = None
    timeout_seconds: float = 2.0


class NodeConfig(BaseModel):
    role: str | None = None
    endpoint: HttpUrl | None = None
    health: HealthCheck = Field(default_factory=HealthCheck)
    tags: list[str] = Field(default_factory=list)


class ModelConfig(BaseModel):
    endpoint: HttpUrl
    model: str
    node: str | None = None
    capabilities: Capabilities = Field(default_factory=Capabilities)
    tags: list[str] = Field(default_factory=list)
    advertised_when: dict[str, Any] = Field(default_factory=dict)


class RouteCondition(BaseModel):
    has_image: bool | None = None
    tools_include: list[str] = Field(default_factory=list)
    input_tokens_gt: int | None = None


class RouteConfig(BaseModel):
    name: str
    when: RouteCondition = Field(default_factory=RouteCondition)
    prefer: list[str] = Field(default_factory=list)
    fallback: list[str] = Field(default_factory=list)


class AdvertisementConfig(BaseModel):
    heartbeat_ttl_seconds: int = 30
    remove_unhealthy_after_seconds: int = 90
    generic_fallback_tags: list[str] = Field(default_factory=lambda: ["text", "local"])


class RouterConfig(BaseModel):
    advertisement: AdvertisementConfig = Field(default_factory=AdvertisementConfig)
    nodes: dict[str, NodeConfig] = Field(default_factory=dict)
    models: dict[str, ModelConfig]
    routes: list[RouteConfig]

    @field_validator("routes")
    @classmethod
    def must_have_default_route(cls, value: list[RouteConfig]) -> list[RouteConfig]:
        if not any(route.name == "default" for route in value):
            raise ValueError("routes must include a default route")
        return value


def load_config(path: str | Path) -> RouterConfig:
    with Path(path).open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    return RouterConfig.model_validate(data)
