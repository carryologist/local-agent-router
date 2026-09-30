# Thinking Flow: RTX 5090 Model Swap

**Date**: 2026-09-30
**Goal**: Replace `granite-4.2-nothink` with `qwen3.8-27b` as the RTX 5090 workhorse model

## Why Granite Was Chosen Originally

The RTX 5090 node was originally configured with `granite-4.2-nothink` as `granite-home`. This was a reasonable choice for a *narrow* home assistant role:

1. **Tool calling reliability** — won the Home Assistant tool-calling bakeoff
2. **Speed** — sub-second responses for HA commands via "nothink" (no chain-of-thought)
3. **Privacy** — home automation data stays local
4. **Optimized for narrow use** — specifically tuned for Home Assistant tool execution

## Why Granite Is the Wrong Model Now

The router config has evolved. The TODO in `the-vibe-coder-content/TODO.md` flagged that the home assistant model should handle more than just HA tasks. The RTX 5090 should serve as a **general-purpose agentic workhorse**, not just a home assistant butler:

- **Researcher** — writing blog posts, analyzing benchmark results, code review
- **IT admin** — homelab maintenance, Docker troubleshooting, log analysis
- **Home assistant butler** — HA commands (existing role)
- **Code assistant** — development work on the-vibe-coder, local-agent-router, etc.

Granite is not competitive with the current state of the art on these broader tasks. It was designed for a narrow use case.

## The Swap: Qwen3.8-27B

**Model**: `Qwen/Qwen3.8-27B`
**Released**: 2026-08-05 (55 days old as of 2026-09-30)
**Architecture**: Dense (non-MoE), 27B parameters
**VRAM**: ~17-22GB depending on quantization (fits comfortably in 32GB VRAM)
**License**: Apache-2.0

### Why Qwen3.8-27B

1. **Superior agentic capabilities** — outperforms Granite across research, coding, and general reasoning benchmarks
2. **Better tool calling** — strong tool calling across diverse tool types (not just HA)
3. **Multimodal** — supports text + vision, though vision is handled by DeepSeek on spark-cluster anyway
4. **Large context** — 131072 token context window vs 65536 for granite
5. **Still fast** — 27B parameters is well within RTX 5090 capabilities, sub-second responses for most tasks
6. **Latest** — most recent Qwen release with strongest agentic performance

### VRAM Breakdown

| Format | VRAM | Headroom |
|--------|------|----------|
| FP8 | ~17GB | 15GB for context |
| FP16 | ~54GB | Too big for single GPU |
| GGUF Q6_K | ~24GB | 8GB for context |
| GGUF Q5_K_M | ~21GB | 11GB for context |
| GGUF Q4_K_M | ~18GB | 14GB for context |

Recommend starting with FP8 if supported by your inference backend (llama.cpp, vLLM, etc.), otherwise GGUF Q5_K_M for best quality.

## Router Changes

### Before
```yaml
rtx-5090:
  role: fast-specialist

models:
  granite-home:
    model: granite-4.2-nothink
    max_context: 65536

routes:
  home_assistant: prefer -> granite-home
  default: prefer -> deepseek-vision (spark-cluster)
```

### After
```yaml
rtx-5090:
  role: workhorse
  tags: [fast_tool_calls, home_assistant, coding, embeddings]

models:
  qwen-workhorse:
    model: qwen3.8-27b
    max_context: 131072

routes:
  home_assistant: prefer -> qwen-workhorse (same)
  default: prefer -> qwen-workhorse (CHANGED)
```

### Impact

- **Home Assistant tasks** — same routing, just a different (better) model
- **Default tasks** — now routed to RTX 5090 locally instead of spark-cluster
  - Reduces latency for common tasks (no network hop to spark-cluster)
  - DeepSeek on spark-cluster is now purely a specialist for vision and very long context
  - Qwen3.8-27b becomes the "good enough" fast model for 80% of tasks

## Files Changed

1. `examples/config.yaml` — model alias, model name, tags, context window, routing defaults
2. `tests/test_routing.py` — renamed `granite-home` -> `qwen-workhorse`, updated test names
3. `tests/test_app.py` — updated inline YAML config, mock model IDs, route assertions
4. `README.md` — updated example reference from granite-home to qwen-workhorse
5. `pyproject.toml` — added pytest/httpx/respx dev dependencies

## Next Steps

1. **Deploy the new config** to the RTX 5090 workstation
2. **Test the swap** — verify HA tasks still work, verify default routing goes to RTX
3. **Run a bakeoff** — compare Qwen3.8-27b vs granite-4.2-nothink on HA tool calling
4. **Monitor latency** — ensure sub-second HA response times are maintained
5. **Consider quantization** — if FP8 isn't supported, test GGUF variants for speed vs quality
