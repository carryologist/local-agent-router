import json
from pathlib import Path

import respx
from fastapi.testclient import TestClient
from httpx import Response

from local_agent_router.app import create_app


CONFIG = """
nodes:
  spark-cluster:
    health:
      url: http://spark.local/health
  rtx-5090:
    health:
      url: http://workstation.local/health

models:
  deepseek-vision:
    node: spark-cluster
    endpoint: http://spark.local/v1
    model: deepseek-ai/DeepSeek-V4-Flash
    capabilities:
      text: true
      vision: true
      tool_calling: true
      max_context: 4096
    tags:
      - local
      - vision

  granite-home:
    node: rtx-5090
    endpoint: http://workstation.local/v1
    model: granite-4.2-30b
    capabilities:
      text: true
      vision: false
      tool_calling: true
      max_context: 65536
    tags:
      - local
      - home_assistant

routes:
  - name: home_assistant
    when:
      tools_include:
        - HassGetState
    prefer:
      - granite-home
    fallback:
      - deepseek-vision

  - name: default
    prefer:
      - deepseek-vision
"""


def test_models_hide_unhealthy_specialist(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("LOCAL_AGENT_ROUTER_HEALTH_TTL_SECONDS", "60")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(CONFIG)

    with respx.mock:
        respx.get("http://spark.local/health").mock(return_value=Response(200))
        respx.get("http://workstation.local/health").mock(return_value=Response(503))
        respx.get("http://spark.local/v1/models").mock(
            return_value=Response(200, json={"data": [{"id": "deepseek-ai/DeepSeek-V4-Flash"}]})
        )

        with TestClient(create_app(str(config_path))) as client:
            response = client.get("/v1/models")
            assert response.status_code == 200
            model_ids = [item["id"] for item in response.json()["data"]]
            assert model_ids == ["deepseek-ai/DeepSeek-V4-Flash"]

            routes = client.get("/routes").json()["routes"]
            ha_route = next(route for route in routes if route["name"] == "home_assistant")
            assert ha_route["degraded"] is True
            assert ha_route["unavailable_preferred"] == ["granite-home"]
            assert ha_route["fallback_available"] == ["deepseek-vision"]


def test_chat_falls_back_when_specialist_offline(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("LOCAL_AGENT_ROUTER_HEALTH_TTL_SECONDS", "60")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(CONFIG)

    with respx.mock:
        respx.get("http://spark.local/health").mock(return_value=Response(200))
        respx.get("http://workstation.local/health").mock(return_value=Response(503))
        respx.get("http://spark.local/v1/models").mock(
            return_value=Response(200, json={"data": [{"id": "deepseek-ai/DeepSeek-V4-Flash"}]})
        )
        chat_route = respx.post("http://spark.local/v1/chat/completions").mock(
            return_value=Response(
                200,
                json={
                    "id": "chatcmpl-test",
                    "object": "chat.completion",
                    "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                },
            )
        )

        with TestClient(create_app(str(config_path))) as client:
            response = client.post(
                "/v1/chat/completions",
                json={
                    "model": "router",
                    "messages": [{"role": "user", "content": "Is the garage open?"}],
                    "tools": [{"type": "function", "function": {"name": "HassGetState"}}],
                },
            )

    assert response.status_code == 200
    assert response.headers["x-local-agent-route"] == "home_assistant"
    assert response.headers["x-local-agent-model-alias"] == "deepseek-vision"
    assert response.headers["x-local-agent-fallback-used"] == "true"
    assert chat_route.calls.last.request.content
    assert json.loads(chat_route.calls.last.request.content)["model"] == "deepseek-ai/DeepSeek-V4-Flash"


def test_chat_retries_fallback_after_backend_500(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("LOCAL_AGENT_ROUTER_HEALTH_TTL_SECONDS", "60")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(CONFIG)

    with respx.mock:
        respx.get("http://spark.local/health").mock(return_value=Response(200))
        respx.get("http://workstation.local/health").mock(return_value=Response(200))
        respx.get("http://spark.local/v1/models").mock(
            return_value=Response(200, json={"data": [{"id": "deepseek-ai/DeepSeek-V4-Flash"}]})
        )
        respx.get("http://workstation.local/v1/models").mock(
            return_value=Response(200, json={"data": [{"id": "granite-4.2-30b"}]})
        )
        granite_route = respx.post("http://workstation.local/v1/chat/completions").mock(
            return_value=Response(500, text="backend unhappy")
        )
        deepseek_route = respx.post("http://spark.local/v1/chat/completions").mock(
            return_value=Response(200, json={"choices": [{"message": {"content": "fallback"}}]})
        )

        with TestClient(create_app(str(config_path))) as client:
            response = client.post(
                "/v1/chat/completions",
                json={
                    "model": "router",
                    "messages": [{"role": "user", "content": "Is the garage open?"}],
                    "tools": [{"type": "function", "function": {"name": "HassGetState"}}],
                },
            )

    assert response.status_code == 200
    assert response.headers["x-local-agent-model-alias"] == "deepseek-vision"
    assert response.headers["x-local-agent-fallback-used"] == "true"
    assert granite_route.called
    assert deepseek_route.called
