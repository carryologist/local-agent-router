# local-agent-router

Local-first policy router for homelab AI fleets.

`local-agent-router` sits between OpenAI-compatible clients and local inference backends. It chooses a backend from request facts and live node availability instead of assuming one model should answer everything.

## Goals

- Expose an OpenAI-compatible `/v1/chat/completions` endpoint.
- Route by deterministic request facts: images, declared tools, input size, and defaults.
- Advertise only currently healthy models from `/v1/models`.
- Fall back to generic local models when a specialist node is offline.
- Keep household policy in YAML instead of hard-coding one machine layout.

## Current status

MVP scaffold. The first implementation includes:

- FastAPI service
- YAML config loading
- backend health checks
- `/health`, `/routes`, `/v1/models`, and `/v1/chat/completions`
- deterministic routing tests

Streaming, search budgets, PAIR discovery, and Hermes-specific plugins are not in the MVP yet.

## Quick start

```bash
cp .env.example .env
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
pytest
local-agent-router --config examples/config.yaml
```

If your workspace has `uv`, the equivalent is:

```bash
uv sync --extra dev
uv run pytest
uv run local-agent-router --config examples/config.yaml
```

The server listens on `0.0.0.0:8088` by default.

## Configuration

See [`examples/config.yaml`](examples/config.yaml). Static config defines allowed nodes, models, and routes. Runtime health decides what is advertised.

If the RTX 5090 workstation is offline, its `granite-home` model disappears from `/v1/models`, `/routes` marks the Home Assistant route as degraded, and Home Assistant requests fall back to the generic DeepSeek route.

## Environment

```bash
LOCAL_AGENT_ROUTER_CONFIG=examples/config.yaml
LOCAL_AGENT_ROUTER_HOST=0.0.0.0
LOCAL_AGENT_ROUTER_PORT=8088
```

## API

- `GET /health` checks backend availability.
- `GET /routes` shows route availability and degraded routes.
- `GET /v1/models` returns healthy advertised models.
- `POST /v1/chat/completions` forwards to the selected backend with the selected backend model name.

## Design decisions

- Python/FastAPI for the MVP.
- LiteLLM is optional downstream infrastructure, not embedded policy.
- Tool authority metadata belongs in router config first.
- PAIR support starts as an OpenAI-compatible backend and can become native discovery later.
- Streaming is out of scope unless pass-through stays simple.
- Classification is deterministic and does not call another LLM.
