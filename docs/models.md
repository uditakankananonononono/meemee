# Model profiles and routing

Meemee talks to any OpenAI-compatible chat endpoint. Since 0.118.0 it can hold several named
model profiles and route each role (`agent`, `chat`, `reflection`) through an ordered fallback
chain. If the first profile fails, the next one is tried, and every attempt is recorded.

Free-first: with no configuration, only the `local` profile (Ollama at
`http://127.0.0.1:11434/v1`) is used, exactly as before. Paid profiles never run unless a key is
set **and** `MEEMEE_ALLOW_PAID_MODELS=true`.

## Built-in profiles

| Profile | What it is | Cost | How to run it |
|---|---|---|---|
| `local` | Your `MEEMEE_MODEL_BASE_URL` / `MEEMEE_MODEL_NAME` (default Ollama `qwen2.5-coder:14b`) | Free | `ollama serve` |
| `inkling` | Thinking Machines Lab **Inkling-Small**, Apache-2.0 open weights (276B MoE / 12B active), via the Hugging Face Inference Providers router | Free HF monthly credits ($0.10 on a free account, $2 on PRO), then prepaid credits. About $0.45-0.50 per 1M input, $1.20 per 1M output | `MEEMEE_HF_TOKEN` |
| `inkling-large` | Full **Inkling** (975B MoE / 41B active) via the HF router | Same credits; about $1.00 in / $4.05 out per 1M | `MEEMEE_HF_TOKEN` |
| `inkling-vllm` | Your own Inkling server (vLLM or SGLang) | Free weights, needs GPU server hardware | `MEEMEE_INKLING_BASE_URL`, optional `MEEMEE_INKLING_SELF_HOSTED_MODEL` |
| `fugu` | Sakana AI **Fugu**, a closed orchestrator that routes across frontier models. Not open weights | Paid Sakana API | `MEEMEE_FUGU_API_KEY`, `MEEMEE_ALLOW_PAID_MODELS=true`, optional `MEEMEE_FUGU_MODEL` (default `fugu-ultra-v2.0`) |

Sources: https://huggingface.co/thinkingmachines/Inkling ,
https://huggingface.co/thinkingmachines/Inkling-Small , https://github.com/SakanaAI/fugu ,
https://console.sakana.ai/pricing

"Ultron" is not a built-in: there is no single model by that name, only several unrelated small
community checkpoints on Hugging Face. Add the one you want as a custom profile.

## Using Inkling (fastest path)

1. Make a free account at https://huggingface.co/join.
2. Settings > Access Tokens > New token > Fine-grained, tick "Make calls to Inference Providers".
3. Export it and route to Inkling with local as the fallback:

```
export MEEMEE_HF_TOKEN=hf_...
export MEEMEE_MODEL_ROUTES="agent=inkling,local;chat=inkling,local;reflection=inkling,local"
meemee models check inkling
```

Requests go to `https://router.huggingface.co/v1` (OpenAI-compatible). HF picks a live provider
(currently baseten or deepinfra for Inkling-Small). To pin one, set
`MEEMEE_HF_INKLING_MODEL=thinkingmachines/Inkling-Small:deepinfra`. When credits run out or the
token is rejected, the call falls back to the next profile in the route and the failure is
recorded. Pricing: https://huggingface.co/docs/inference-providers/en/pricing

## Routes

```
MEEMEE_MODEL_ROUTES="agent=inkling,local;chat=local;reflection=inkling-large,local"
```

Unknown roles or profile names fail at startup. Roles not listed default to `local`.

## Custom profiles

`MEEMEE_MODEL_PROFILES` takes a JSON list or a path to a JSON file:

```json
[
  {"name": "ultron-7b", "base_url": "http://127.0.0.1:8001/v1", "model": "passing2961/Ultron-7B", "kind": "self_hosted"},
  {"name": "my-hosted", "base_url": "https://example.test/v1", "model": "m", "kind": "hosted_free", "api_key_env": "MY_KEY"}
]
```

`kind` is one of `local`, `self_hosted`, `hosted_free`, `hosted_paid`. Hosted kinds require a key
by default; `api_key_env` reads it from the environment so it never sits in the file.

## CLI

```
meemee models list          # every profile, usable or not and why, plus active routes
meemee models check [names] # probes each /models endpoint; never sends a completion
```

`check` exits nonzero when no probed profile is reachable.
