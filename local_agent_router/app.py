from __future__ import annotations

import argparse
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from .config import RouterConfig, load_config
from .health import HealthSnapshot, check_health
from .routing import choose_route

LOGGER = logging.getLogger("local_agent_router")


class AppState:
    def __init__(self, config: RouterConfig) -> None:
        self.config = config
        self.health = HealthSnapshot(
            healthy_nodes=set(config.nodes),
            healthy_models=set(config.models),
        )


async def create_app(config_path: str | None = None) -> FastAPI:
    path = config_path or os.getenv("LOCAL_AGENT_ROUTER_CONFIG", "examples/config.yaml")
    state = AppState(load_config(path))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.router = state
        app.state.router.health = await check_health(state.config)
        yield

    app = FastAPI(title="local-agent-router", version="0.1.0", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, Any]:
        state.health = await check_health(state.config)
        return {
            "ok": bool(state.health.healthy_models),
            "healthy_nodes": sorted(state.health.healthy_nodes),
            "healthy_models": sorted(state.health.healthy_models),
        }

    @app.get("/routes")
    async def routes() -> dict[str, Any]:
        state.health = await check_health(state.config)
        route_views = []
        for route in state.config.routes:
            preferred = [alias for alias in route.prefer if alias in state.health.healthy_models]
            fallback = [alias for alias in route.fallback if alias in state.health.healthy_models]
            route_views.append(
                {
                    "name": route.name,
                    "preferred_available": preferred,
                    "fallback_available": fallback,
                    "degraded": not preferred and bool(route.prefer),
                }
            )
        return {"routes": route_views}

    @app.get("/v1/models")
    async def models() -> dict[str, Any]:
        state.health = await check_health(state.config)
        return {
            "object": "list",
            "data": [
                {
                    "id": model.model,
                    "object": "model",
                    "owned_by": "local-agent-router",
                    "metadata": {
                        "alias": alias,
                        "tags": model.tags,
                        "capabilities": model.capabilities.model_dump(),
                    },
                }
                for alias, model in state.config.models.items()
                if alias in state.health.healthy_models
            ],
        }

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request) -> JSONResponse:
        payload = await request.json()
        state.health = await check_health(state.config)
        try:
            decision = choose_route(state.config, payload, state.health.healthy_models)
        except RuntimeError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

        forwarded = dict(payload)
        forwarded["model"] = decision.selected_model
        started = time.perf_counter()
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    f"{decision.endpoint.rstrip('/')}/chat/completions",
                    json=forwarded,
                    headers=_forward_headers(request),
                    timeout=None,
                )
            except httpx.HTTPError as error:
                raise HTTPException(status_code=502, detail=str(error)) from error

        latency_ms = round((time.perf_counter() - started) * 1000)
        LOGGER.info(
            "route=%s selected_alias=%s model=%s reason=%s fallback_used=%s latency_ms=%s",
            decision.route,
            decision.selected_alias,
            decision.selected_model,
            decision.reason,
            decision.fallback_used,
            latency_ms,
        )
        return JSONResponse(
            content=response.json(),
            status_code=response.status_code,
            headers={
                "x-local-agent-route": decision.route,
                "x-local-agent-model-alias": decision.selected_alias,
                "x-local-agent-fallback-used": str(decision.fallback_used).lower(),
            },
        )

    return app


def _forward_headers(request: Request) -> dict[str, str]:
    headers: dict[str, str] = {}
    if auth := request.headers.get("authorization"):
        headers["authorization"] = auth
    return headers


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=os.getenv("LOCAL_AGENT_ROUTER_CONFIG", "examples/config.yaml"))
    parser.add_argument("--host", default=os.getenv("LOCAL_AGENT_ROUTER_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.getenv("LOCAL_AGENT_ROUTER_PORT", "8088")))
    args = parser.parse_args()
    uvicorn.run("local_agent_router.app:create_app", host=args.host, port=args.port, factory=True)


if __name__ == "__main__":
    main()
