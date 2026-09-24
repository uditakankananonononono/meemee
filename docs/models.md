# Model profiles and routing

Meemee talks to any OpenAI-compatible chat endpoint. Since 0.118.0 it can hold several named
model profiles and route each role (`agent`, `chat`, `reflection`) through an ordered fallback
chain. If the first profile fails, the next one is tried, and every attempt is recorded.

Free-first: with no configuration, only the `local` profile (Ollama at
`http://127.0.0.1:11434/v1`) is used, exactly as before. Paid profiles never run unless a key is
set **and** `MEEMEE_ALLOW_PAID_MODELS=true`.

Default route: `local`, then `inkling` on the Hugging Face router **only when `MEEMEE_HF_TOKEN` is
set**. That fallback is metered: it spends the token's Inference Providers credits (free monthly
credits, then only credits you have prepaid). Set `MEEMEE_HF_FALLBACK=false` to keep every role
local-only even with a token. An explicit `MEEMEE_MODEL_ROUTES` entry always wins for its role.

Every profile has a `transport`: `openai` (any OpenAI-compatible HTTP API: Ollama, vLLM, SGLang,
llama.cpp server, LM Studio, the HF router, Sakana) or `transformers` (weights run inside the
Meemee process). Both give the same `decide`/`chat` interface, so they mix freely in one route.

## Built-in profiles

| Profile | What it is | Cost | How to run it |
|---|---|---|---|
| `local` | Your `MEEMEE_MODEL_BASE_URL` / `MEEMEE_MODEL_NAME` (default Ollama `qwen2.5-coder:14b`) | Free | `ollama serve` |
| `inkling` | Thinking Machines Lab **Inkling-Small**, Apache-2.0 open weights (276B MoE / 12B active), via the Hugging Face Inference Providers router | Free HF monthly credits ($0.10 on a free account, $2 on PRO), then prepaid credits. About $0.45-0.50 per 1M input, $1.20 per 1M output | `MEEMEE_HF_TOKEN` |
| `inkling-large` | Full **Inkling** (975B MoE / 41B active) via the HF router | Same credits; about $1.00 in / $4.05 out per 1M | `MEEMEE_HF_TOKEN` |
| `inkling-vllm` | Your own Inkling server (vLLM or SGLang) | Free weights, needs GPU server hardware | `MEEMEE_INKLING_BASE_URL`, optional `MEEMEE_INKLING_SELF_HOSTED_MODEL` |
| `local-transformers` | Any open-weight chat model run in-process with Hugging Face `transformers` (`MEEMEE_TRANSFORMERS_MODEL`, default `Qwen/Qwen2.5-0.5B-Instruct`) on CPU, CUDA or MPS. No model server | Free | `pip install 'meemee[transformers]'`, then `meemee models pull local-transformers` |
| `fugu` | Sakana AI **Fugu**, a closed orchestrator that routes across frontier models. Not open weights | Paid Sakana API | `MEEMEE_FUGU_API_KEY` (or Sakana's `SAKANA_API_KEY`), `MEEMEE_ALLOW_PAID_MODELS=true`, optional `MEEMEE_FUGU_MODEL` (default `fugu-ultra`, Sakana's alias for the latest Fugu Ultra; pinned ids: `fugu`, `fugu-ultra-v1.0`, `fugu-ultra-v1.1`, `fugu-cyber-v1.0`) |

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

## In-process open weights (`local-transformers`)

For a PC without Ollama, or as a last local fallback:

```
pip install 'meemee[transformers]'
export MEEMEE_TRANSFORMERS_MODEL=Qwen/Qwen2.5-0.5B-Instruct   # any HF chat model id or local directory
meemee models pull local-transformers                        # one-time download into the HF cache
export MEEMEE_MODEL_ROUTES="agent=local,local-transformers;chat=local,local-transformers"
```

Weights are never downloaded during a request: an unpulled model fails that attempt with a
message naming `meemee models pull`, and the route moves on. `MEEMEE_TRANSFORMERS_DEVICE` is
`auto` (CUDA, then MPS, then CPU), `cpu`, `cuda` or `mps`; `MEEMEE_TRANSFORMERS_MAX_NEW_TOKENS`
defaults to 512. Loaded weights stay cached in the process. Agent decisions use greedy decoding
and take the first JSON object in the output, so small models that wrap JSON in prose still work.
Inkling itself (276B/975B MoE) is far too large for this path; use the HF router or a GPU server
(`inkling-vllm`, see `meemee models inkling-local`).

Custom profiles take `"transport": "transformers"` and then need no `base_url`:

```json
[{"name": "qwen-7b-local", "model": "Qwen/Qwen2.5-7B-Instruct", "kind": "local", "transport": "transformers"}]
```

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

## HTTP

`GET /v1/models/status` (token scope `jobs:read`) returns every profile with `available` and
`unavailable_reason`, the active routes and `allow_paid_models`. Keys are never returned, only
`key_configured`. Add `?probe=true` to check each routed profile's `/models` endpoint (3s timeout,
no tokens spent). The response then includes `probes` and `serving`, which is the first reachable
profile for each role.

## CLI

```
meemee models list          # every profile, usable or not and why, plus active routes
meemee models check [names] # probes each /models endpoint; never sends a completion
meemee models pull <name>   # downloads a transformers-transport profile's weights once
```

For `transformers` profiles `check` makes no network call: it reports whether the libraries are
installed and the weights are already on disk.

`check` exits nonzero when no probed profile is reachable.

## Shared model layer (instinct_models)

Meemee, Atlas and Sugarcode share one model package, `instinct_models`
(https://github.com/uditakankananonononono/shared-models). Meemee vendors it under
`meemee/_vendor/instinct_models` at the commit recorded in `meemee/_vendor/INSTINCT_MODELS_PIN`.
To move to a newer commit run `scripts/sync_instinct_models.sh <commit>`.

Use it through the `shared` profile:

    MEEMEE_SHARED_ORNITH_URL=http://localhost:11434/v1   # Ornith-1.5-9B GGUF in Ollama/llama.cpp
    MEEMEE_SHARED_ORNITH_MODEL=<tag you pulled>
    MEEMEE_SHARED_INKLING_URL=...                        # optional self-hosted Inkling
    MEEMEE_SHARED_NEEDLE_WEIGHTS=models/meemee-tuned.cact  # optional Meemee-tuned Needle
    MEEMEE_MODEL_ROUTES="chat=shared,local"

The hosted Hugging Face router (metered past its free credit) is only used when
`MEEMEE_SHARED_ALLOW_HOSTED=true`, because Meemee handles personal data.

Training on Meemee data only: write owner-confirmed examples as JSONL
(`query`, `tools`, `answers`, `confirmed`, `source_ref`) and call
`meemee.shared_models.train_meemee_needle(examples, out_dir)`. It needs `pip install cactus-needle`.
Unconfirmed rows and rows tagged for another product are dropped. Needle telemetry is
turned off by the shared layer.
