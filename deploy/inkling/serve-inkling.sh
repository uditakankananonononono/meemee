#!/usr/bin/env bash
# One-command local Inkling-Small server for Meemee.
#   ./deploy/inkling/serve-inkling.sh            # check hardware, pick the best plan, start
#   ./deploy/inkling/serve-inkling.sh --docker   # vLLM plans via the official vllm-openai image
#   PLAN=llamacpp-q3 ./deploy/inkling/serve-inkling.sh
# Always real Inkling-Small. Refuses to start below the hardware floor unless FORCE=1.
set -euo pipefail
PLAN="${PLAN:-auto}"
PORT="${PORT:-8000}"
MODEL_DIR="${MODEL_DIR:-models/inkling-small}"
FORCE_FLAG=""; [ "${FORCE:-0}" = "1" ] && FORCE_FLAG="--force"

report="$(meemee models inkling-local --plan "$PLAN" --port "$PORT" --model-dir "$MODEL_DIR" $FORCE_FLAG)"
echo "$report"
chosen="$(printf '%s' "$report" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("plan") or "")')"
if [ -z "$chosen" ]; then
  echo "No plan fits this machine. Use the Hugging Face 'inkling' profile until the hardware exists." >&2
  exit 2
fi

if [ "${1:-}" = "--docker" ] && [[ "$chosen" == vllm-* ]]; then
  weights="$(printf '%s' "$report" | python3 -c 'import json,sys; print(json.load(sys.stdin)["plans"][sys.argv[1]]["weights"])' "$chosen")"
  tp="$(printf '%s' "$report" | python3 -c 'import json,sys; a=json.load(sys.stdin)["launch"]; print(a[a.index("--tensor-parallel-size")+1])')"
  exec docker run --gpus all --privileged --ipc=host -p "127.0.0.1:${PORT}:8000" \
    -v "${HOME}/.cache/huggingface:/root/.cache/huggingface" \
    -e VLLM_USE_V2_MODEL_RUNNER=1 -e FLASH_ATTENTION_CUTE_DSL_CACHE_ENABLED=1 \
    vllm/vllm-openai:nightly "$weights" \
    --served-model-name thinkingmachines/Inkling-Small \
    --trust-remote-code --tokenizer-mode inkling \
    --kernel-config.enable_flashinfer_autotune=False \
    --tensor-parallel-size "$tp" \
    --enable-auto-tool-choice --tool-call-parser inkling --reasoning-parser inkling
fi

exec meemee models inkling-local --plan "$chosen" --port "$PORT" --model-dir "$MODEL_DIR" --run $FORCE_FLAG
