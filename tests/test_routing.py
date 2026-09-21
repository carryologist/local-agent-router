from local_agent_router.config import RouterConfig
from local_agent_router.routing import choose_route, extract_request_facts


CONFIG = RouterConfig.model_validate(
    {
        "models": {
            "deepseek-vision": {
                "endpoint": "http://spark.local:8000/v1",
                "model": "deepseek-ai/DeepSeek-V4-Flash",
                "capabilities": {"vision": True, "tool_calling": True, "max_context": 4096},
                "tags": ["vision", "local"],
            },
            "granite-home": {
                "endpoint": "http://workstation.local:8080/v1",
                "model": "granite-4.2-30b",
                "capabilities": {"tool_calling": True, "max_context": 65536},
                "tags": ["home_assistant", "local"],
            },
        },
        "routes": [
            {"name": "vision", "when": {"has_image": True}, "prefer": ["deepseek-vision"]},
            {
                "name": "home_assistant",
                "when": {"tools_include": ["HassGetState"]},
                "prefer": ["granite-home"],
                "fallback": ["deepseek-vision"],
            },
            {"name": "default", "prefer": ["deepseek-vision"]},
        ],
    }
)


def test_extracts_image_from_multimodal_content():
    facts = extract_request_facts(
        {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "What is this?"},
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}},
                    ],
                }
            ]
        }
    )
    assert facts.has_image is True


def test_home_assistant_prefers_granite_when_available():
    decision = choose_route(
        CONFIG,
        {
            "messages": [{"role": "user", "content": "Is the garage door open?"}],
            "tools": [{"type": "function", "function": {"name": "HassGetState"}}],
        },
        healthy_models={"granite-home", "deepseek-vision"},
    )
    assert decision.route == "home_assistant"
    assert decision.selected_alias == "granite-home"
    assert decision.fallback_used is False


def test_offline_specialist_falls_back_to_generic_model():
    decision = choose_route(
        CONFIG,
        {
            "messages": [{"role": "user", "content": "Is the garage door open?"}],
            "tools": [{"type": "function", "function": {"name": "HassGetState"}}],
        },
        healthy_models={"deepseek-vision"},
    )
    assert decision.route == "home_assistant"
    assert decision.selected_alias == "deepseek-vision"
    assert decision.fallback_used is True
    assert decision.degraded is True


def test_default_uses_generic_model():
    decision = choose_route(
        CONFIG,
        {"messages": [{"role": "user", "content": "hello"}]},
        healthy_models={"deepseek-vision"},
    )
    assert decision.route == "default"
    assert decision.selected_alias == "deepseek-vision"
