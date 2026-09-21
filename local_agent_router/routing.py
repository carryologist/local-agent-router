from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import RouteConfig, RouterConfig


@dataclass(frozen=True)
class RequestFacts:
    has_image: bool
    tools: set[str]
    estimated_input_tokens: int


@dataclass(frozen=True)
class RouteDecision:
    route: str
    selected_alias: str
    selected_model: str
    endpoint: str
    reason: str
    fallback_used: bool
    degraded: bool


def extract_request_facts(payload: dict[str, Any]) -> RequestFacts:
    return RequestFacts(
        has_image=_payload_has_image(payload),
        tools=_extract_tools(payload),
        estimated_input_tokens=_estimate_input_tokens(payload),
    )


def choose_route(
    config: RouterConfig,
    payload: dict[str, Any],
    healthy_models: set[str] | None = None,
) -> RouteDecision:
    healthy_models = set(config.models) if healthy_models is None else healthy_models
    facts = extract_request_facts(payload)
    route, reason = _matching_route(config.routes, facts)

    candidates = list(route.prefer) + list(route.fallback)
    for alias in candidates:
        if alias in healthy_models and alias in config.models:
            model = config.models[alias]
            fallback_used = alias not in route.prefer
            return RouteDecision(
                route=route.name,
                selected_alias=alias,
                selected_model=model.model,
                endpoint=str(model.endpoint),
                reason=reason,
                fallback_used=fallback_used,
                degraded=fallback_used,
            )

    default = next(item for item in config.routes if item.name == "default")
    for alias in list(default.prefer) + list(default.fallback):
        if alias in healthy_models and alias in config.models:
            model = config.models[alias]
            return RouteDecision(
                route=route.name,
                selected_alias=alias,
                selected_model=model.model,
                endpoint=str(model.endpoint),
                reason=f"{reason}; preferred models unavailable; generic fallback",
                fallback_used=True,
                degraded=True,
            )

    raise RuntimeError("no healthy model is available for this request")


def _matching_route(routes: list[RouteConfig], facts: RequestFacts) -> tuple[RouteConfig, str]:
    default_route = None
    for route in routes:
        if route.name == "default":
            default_route = route
            continue
        matches, reason = _route_matches(route, facts)
        if matches:
            return route, reason
    if default_route is None:
        raise RuntimeError("no default route configured")
    return default_route, "default route"


def _route_matches(route: RouteConfig, facts: RequestFacts) -> tuple[bool, str]:
    condition = route.when
    reasons: list[str] = []
    if condition.has_image is not None:
        if facts.has_image != condition.has_image:
            return False, ""
        reasons.append("has_image")
    if condition.tools_include:
        matched_tools = sorted(facts.tools.intersection(condition.tools_include))
        if not matched_tools:
            return False, ""
        reasons.append("tools_include " + ",".join(matched_tools))
    if condition.input_tokens_gt is not None:
        if facts.estimated_input_tokens <= condition.input_tokens_gt:
            return False, ""
        reasons.append(f"input_tokens_gt {condition.input_tokens_gt}")
    return bool(reasons), "; ".join(reasons)


def _payload_has_image(payload: dict[str, Any]) -> bool:
    for message in payload.get("messages", []):
        content = message.get("content")
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") in {"image_url", "input_image"}:
                    return True
                if isinstance(part, dict) and "image_url" in part:
                    return True
    return False


def _extract_tools(payload: dict[str, Any]) -> set[str]:
    tools: set[str] = set()
    for tool in payload.get("tools", []) or []:
        if not isinstance(tool, dict):
            continue
        function = tool.get("function") or {}
        if isinstance(function, dict) and function.get("name"):
            tools.add(str(function["name"]))
        elif tool.get("name"):
            tools.add(str(tool["name"]))
    for function in payload.get("functions", []) or []:
        if isinstance(function, dict) and function.get("name"):
            tools.add(str(function["name"]))
    return tools


def _estimate_input_tokens(payload: dict[str, Any]) -> int:
    # Cheap deterministic estimate. Good enough for routing thresholds without
    # making a tokenizer or classifier call part of the critical path.
    chars = 0
    for message in payload.get("messages", []):
        content = message.get("content", "")
        if isinstance(content, str):
            chars += len(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    value = part.get("text") or part.get("content") or ""
                    if isinstance(value, str):
                        chars += len(value)
    return max(1, chars // 4) if chars else 0
