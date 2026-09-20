from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import httpx

from .config import RouterConfig


@dataclass(frozen=True)
class HealthSnapshot:
    healthy_nodes: set[str]
    healthy_models: set[str]


class HealthCache:
    def __init__(self, config: RouterConfig, ttl_seconds: float = 5.0) -> None:
        self.config = config
        self.ttl_seconds = ttl_seconds
        self._snapshot = HealthSnapshot(
            healthy_nodes=set(config.nodes),
            healthy_models=set(config.models),
        )
        self._checked_at = 0.0
        self._lock = asyncio.Lock()

    @property
    def snapshot(self) -> HealthSnapshot:
        return self._snapshot

    async def get(self, *, force: bool = False) -> HealthSnapshot:
        now = time.monotonic()
        if not force and now - self._checked_at < self.ttl_seconds:
            return self._snapshot
        async with self._lock:
            now = time.monotonic()
            if not force and now - self._checked_at < self.ttl_seconds:
                return self._snapshot
            self._snapshot = await check_health(self.config)
            self._checked_at = time.monotonic()
            return self._snapshot


async def check_health(config: RouterConfig) -> HealthSnapshot:
    healthy_nodes = await _healthy_nodes(config)
    healthy_models: set[str] = set()
    async with httpx.AsyncClient() as client:
        checks = [
            _model_available(client, alias, model.model, str(model.endpoint))
            for alias, model in config.models.items()
            if model.node is None or model.node in healthy_nodes
        ]
        for alias, ok in await asyncio.gather(*checks):
            if ok:
                healthy_models.add(alias)
    return HealthSnapshot(healthy_nodes=healthy_nodes, healthy_models=healthy_models)


async def _healthy_nodes(config: RouterConfig) -> set[str]:
    async with httpx.AsyncClient() as client:
        checks = []
        for name, node in config.nodes.items():
            if node.health.url is None:
                checks.append(_static_node(name, True))
            else:
                checks.append(_http_ok(client, name, str(node.health.url), node.health.timeout_seconds))
        results = await asyncio.gather(*checks)
    return {name for name, ok in results if ok}


async def _static_node(name: str, ok: bool) -> tuple[str, bool]:
    return name, ok


async def _http_ok(client: httpx.AsyncClient, name: str, url: str, timeout: float) -> tuple[str, bool]:
    try:
        response = await client.get(url, timeout=timeout)
        return name, response.status_code < 500
    except httpx.HTTPError:
        return name, False


async def _model_available(
    client: httpx.AsyncClient,
    alias: str,
    model_name: str,
    endpoint: str,
) -> tuple[str, bool]:
    try:
        response = await client.get(f"{endpoint.rstrip('/')}/models", timeout=2.0)
        if response.status_code >= 500:
            return alias, False
        data = response.json()
        ids = {str(item.get("id")) for item in data.get("data", []) if isinstance(item, dict)}
        return alias, not ids or model_name in ids
    except httpx.HTTPError:
        return alias, False
    except ValueError:
        return alias, True
