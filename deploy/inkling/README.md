# Running Inkling-Small on your own hardware

This is the real `thinkingmachines/Inkling-Small` (276B-parameter MoE, 12B active per token,
Apache-2.0). Every plan below serves that same model. Only the engine and how tightly the
weights are compressed change.

## Hardware floor

| Plan | Engine | Weights | Needs | Typical machine |
|---|---|---|---|---|
| `vllm-nvfp4` | vLLM | `Inkling-Small-NVFP4` | 180GB+ aggregated NVIDIA VRAM | 1x B300, or 2x B200/H200 |
| `vllm-bf16` | vLLM | `Inkling-Small` (BF16) | 600GB+ VRAM | 4x B300 or 8x H200 |
| `llamacpp-q4` | llama.cpp | Unsloth `UD-Q4_K_XL` GGUF, 163.3GB | ~175GB RAM+VRAM, 164GB disk | 192GB Mac Studio, or a 192-256GB RAM workstation |
| `llamacpp-q3` | llama.cpp | `UD-Q3_K_XL`, 119.6GB | ~130GB RAM+VRAM | 128GB+ RAM plus a GPU |
| `llamacpp-q2` | llama.cpp | `UD-Q2_K_XL`, 87.9GB | ~100GB RAM+VRAM | 128GB RAM machine (lowest quality) |

A typical laptop or gaming PC (16-64GB RAM, one consumer GPU) is below every floor. A model this
size does not fit in that memory, and swapping it from disk is unusably slow. For those
machines, use the `inkling` profile (Hugging Face router) instead.

Sources: https://recipes.vllm.ai/thinkingmachines/Inkling-Small ,
https://huggingface.co/unsloth/Inkling-Small-GGUF , https://unsloth.ai/docs/models/inkling

## One command

```
pip install -e .                  # Meemee
pip install -U "huggingface_hub[cli]"   # for GGUF downloads
# vLLM plans: pip install vllm  (or use --docker with the official image)
# llama.cpp plans: build llama.cpp so llama-server is on PATH

meemee models inkling-local       # check only: hardware, best plan, exact commands
./deploy/inkling/serve-inkling.sh # download if needed and start on 127.0.0.1:8000
```

Then point Meemee at it, keeping the HF route and local Qwen as fallbacks:

```
export MEEMEE_INKLING_BASE_URL=http://127.0.0.1:8000/v1
export MEEMEE_MODEL_ROUTES="agent=inkling-vllm,inkling,local;chat=inkling-vllm,inkling,local"
meemee models check inkling-vllm
```

The server is published as `thinkingmachines/Inkling-Small` whatever the plan, so the
`inkling-vllm` profile matches it with no extra config.

## Known issue

A vLLM bug report says JSON-schema `response_format` on the NVFP4 checkpoint can return HTTP 500
(https://github.com/vllm-project/vllm/issues/51693). Meemee asks for
`json_object` mode, not a JSON schema. If a 500 happens anyway, the router retries and then falls
back to the next profile in the route.
