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
            "qwen-workhorse": {
                "endpoint": "http://workstation.local:8080/v1",
                "model": "qwen3.8-27b",
                "capabilities": {"tool_calling": True, "max_context": 131072},
                "tags": ["home_assistant", "coding", "local"],
            },
        },
        "routes": [
            {"name": "vision", "when": {"has_image": True}, "prefer": ["deepseek-vision"]},
            {
                "name": "home_assistant",
                "when": {"tools_include": ["HassGetState"]},
                "prefer": ["qwen-workhorse"],
                "fallback": ["deepseek-vision"],
            },
            {"name": "default", "prefer": ["qwen-workhorse"]},
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


def test_home_assistant_prefers_qwen_when_available():
    decision = choose_route(
        CONFIG,
        {
            "messages": [{"role": "user", "content": "Is the garage door open?"}],
            "tools": [{"type": "function", "function": {"name": "HassGetState"}}],
        },
        healthy_models={"qwen-workhorse", "deepseek-vision"},
    )
    assert decision.route == "home_assistant"
    assert decision.selected_alias == "qwen-workhorse"
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


def test_default_uses_qwen_as_primary():
    decision = choose_route(
        CONFIG,
        {"messages": [{"role": "user", "content": "hello"}]},
        healthy_models={"qwen-workhorse", "deepseek-vision"},
    )
    assert decision.route == "default"
    assert decision.selected_alias == "qwen-workhorse"
