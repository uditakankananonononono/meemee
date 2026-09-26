"""Per-product configuration from env, optionally overlaid by a small YAML/JSON file.

Env (prefix INSTINCT_):
  INSTINCT_PRODUCT             atlas | meemee | sugarcode (required)
  INSTINCT_INKLING_LOCAL_URL   OpenAI-compatible base URL for self-hosted Inkling (llama.cpp/vLLM/SGLang)
  INSTINCT_INKLING_LOCAL_MODEL model name served there (default inkling-small)
  INSTINCT_HF_MODEL            HF router model id (default thinkingmachines/Inkling-Small); token from HF_TOKEN
  INSTINCT_ORNITH_URL          OpenAI-compatible base URL (Ollama: http://localhost:11434/v1)
  INSTINCT_ORNITH_MODEL        model tag as pulled locally (no default: must match what she pulled)
  INSTINCT_NEEDLE_WEIGHTS      path to a product .cact (tuned) - empty means the base Needle model
  INSTINCT_ALLOW_HOSTED        1 to allow metered hosted HF router for non-private tasks (default 0)
  INSTINCT_JEV_API_KEY         TypeSafe AI direct evaluation API key (optional; falls back to JEV_API_KEY).
  INSTINCT_AI_GATEWAY_API_KEY  Vercel AI Gateway key for Jev (optional; falls back to AI_GATEWAY_API_KEY).
  INSTINCT_HERMES_URL         local Ollama OpenAI-compatible /v1 URL (opt-in)
  INSTINCT_HERMES_MODEL       pulled Hermes tag, e.g. hermes3:3b
                               Jev is hosted and key-gated (paid credits); empty keeps it OFF.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, replace
from pathlib import Path

PRODUCTS = ("atlas", "meemee", "sugarcode")


@dataclass(frozen=True)
class ProductConfig:
    product: str
    inkling_local_url: str | None = None
    inkling_local_model: str = "inkling-small"
    hf_model: str = "thinkingmachines/Inkling-Small"
    ornith_url: str | None = None
    ornith_model: str | None = None
    needle_weights: str | None = None
    hermes_url: str | None = None
    hermes_model: str | None = None
    allow_hosted: bool = False
    jev_api_key: str | None = None
    extra: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.product not in PRODUCTS:
            raise ValueError(f"product must be one of {PRODUCTS}, got {self.product!r}")


def _load_file(path: str) -> dict:
    text = Path(path).read_text()
    if path.endswith((".yaml", ".yml")):
        try:
            import yaml  # optional
        except ImportError as exc:
            raise ValueError("YAML config needs PyYAML; use JSON instead") from exc
        return yaml.safe_load(text) or {}
    return json.loads(text)


def load_config(env: dict | None = None, path: str | None = None) -> ProductConfig:
    e = os.environ if env is None else env
    g = lambda k, d=None: (e.get(f"INSTINCT_{k}") or d)
    cfg = ProductConfig(product=g("PRODUCT", ""), inkling_local_url=g("INKLING_LOCAL_URL"),
                        inkling_local_model=g("INKLING_LOCAL_MODEL", "inkling-small"),
                        hf_model=g("HF_MODEL", "thinkingmachines/Inkling-Small"), ornith_url=g("ORNITH_URL"),
                        ornith_model=g("ORNITH_MODEL"), needle_weights=g("NEEDLE_WEIGHTS"), hermes_url=g("HERMES_URL"),
                        hermes_model=g("HERMES_MODEL"),
                        allow_hosted=g("ALLOW_HOSTED", "0") not in ("0", "false", "no"),
                        jev_api_key=g("JEV_API_KEY") or e.get("JEV_API_KEY") or None)
    if path:
        data = _load_file(path)
        known = {k: v for k, v in data.items() if k in ProductConfig.__dataclass_fields__ and k != "extra"}
        cfg = replace(cfg, **known, extra={k: v for k, v in data.items() if k not in known})
    return cfg
