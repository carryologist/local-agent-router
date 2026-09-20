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
from fastapi.responses import JSONResponse, Response

from .config import RouterConfig, load_config
from .health import HealthCache, HealthSnapshot
from .routing import RouteDecision, choose_route

LOGGER = logging.getLogger("local_agent_router")


class AppState:
    def __init__(self, config: RouterConfig) -> None:
        self.config = config
        ttl = float(os.getenv("LOCAL_AGENT_ROUTER_HEALTH_TTL_SECONDS", "5"))
        self.health_cache = HealthCache(config, ttl_seconds=ttl)

    @property
    def health(self) -> HealthSnapshot:
        return self.health_cache.snapshot


def create_app(config_path: str | None = None) -> FastAPI:
    path = config_path or os.getenv("LOCAL_AGENT_ROUTER_CONFIG", "examples/config.yaml")
    state = AppState(load_config(path))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.router = state
        await app.state.router.health_cache.get(force=True)
        yield

    app = FastAPI(title="local-agent-router", version="0.1.0", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, Any]:
        snapshot = await state.health_cache.get(force=True)
        return {
            "ok": bool(snapshot.healthy_models),
            "healthy_nodes": sorted(snapshot.healthy_nodes),
            "healthy_models": sorted(snapshot.healthy_models),
        }

    @app.get("/routes")
    async def routes() -> dict[str, Any]:
        snapshot = await state.health_cache.get()
        route_views = []
        for route in state.config.routes:
            preferred = [alias for alias in route.prefer if alias in snapshot.healthy_models]
            fallback = [alias for alias in route.fallback if alias in snapshot.healthy_models]
            unavailable_preferred = [alias for alias in route.prefer if alias not in snapshot.healthy_models]
            route_views.append(
                {
                    "name": route.name,
                    "preferred_available": preferred,
                    "fallback_available": fallback,
                    "unavailable_preferred": unavailable_preferred,
                    "degraded": not preferred and bool(route.prefer),
                    "reason": _route_status_reason(preferred, fallback, unavailable_preferred),
                }
            )
        return {"routes": route_views}

    @app.get("/v1/models")
    async def models() -> dict[str, Any]:
        snapshot = await state.health_cache.get()
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
                if alias in snapshot.healthy_models
            ],
        }

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request) -> Response:
        payload = await request.json()
        snapshot = await state.health_cache.get()
        remaining_models = set(snapshot.healthy_models)
        attempts: list[RouteDecision] = []
        last_error = "no backend attempted"

        while remaining_models:
            try:
                decision = choose_route(state.config, payload, remaining_models)
            except RuntimeError as error:
                last_error = str(error)
                break
            attempts.append(decision)
            response = await _forward_chat_request(request, payload, decision)
            if response is not None:
                return response
            remaining_models.discard(decision.selected_alias)
            last_error = f"backend {decision.selected_alias} failed"

        raise HTTPException(
            status_code=502,
            detail={
                "error": last_error,
                "attempted_models": [attempt.selected_alias for attempt in attempts],
            },
        )

    return app


async def _forward_chat_request(
    request: Request,
    payload: dict[str, Any],
    decision: RouteDecision,
) -> Response | None:
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
        except httpx.HTTPError:
            LOGGER.warning("backend_request_failed alias=%s", decision.selected_alias, exc_info=True)
            return None

    latency_ms = round((time.perf_counter() - started) * 1000)
    LOGGER.info(
        "route=%s selected_alias=%s model=%s reason=%s fallback_used=%s latency_ms=%s status=%s",
        decision.route,
        decision.selected_alias,
        decision.selected_model,
        decision.reason,
        decision.fallback_used,
        latency_ms,
        response.status_code,
    )
    if response.status_code >= 500:
        return None
    headers = {
        "x-local-agent-route": decision.route,
        "x-local-agent-model-alias": decision.selected_alias,
        "x-local-agent-fallback-used": str(decision.fallback_used).lower(),
    }
    content_type = response.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            return JSONResponse(
                content=response.json(),
                status_code=response.status_code,
                headers=headers,
            )
        except ValueError:
            if response.status_code >= 400:
                return JSONResponse(
                    content={"error": response.text},
                    status_code=response.status_code,
                    headers=headers,
                )
    return Response(
        content=response.content,
        status_code=response.status_code,
        media_type=content_type or None,
        headers=headers,
    )


def _route_status_reason(
    preferred: list[str], fallback: list[str], unavailable_preferred: list[str]
) -> str:
    if preferred:
        return "preferred model available"
    if fallback:
        return "preferred model unavailable; fallback available"
    if unavailable_preferred:
        return "preferred model unavailable; no route-specific fallback available"
    return "no preferred models configured"


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
    app = create_app(args.config)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
