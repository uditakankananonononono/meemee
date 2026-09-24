"""In-process open-weight models through Hugging Face ``transformers``.

This is the no-server local path: the weights run inside the Meemee process on the
user's own machine (CPU, CUDA or Apple MPS), with no Ollama or vLLM needed. It has the
same interface as :class:`meemee.llm.OpenAICompatibleModel` (``decide``, ``chat``,
``aclose``), so a ``transport: "transformers"`` profile can sit anywhere in a route.

Weights are never downloaded during a request. ``meemee models pull <profile>`` fetches
them once; after that every load uses ``local_files_only=True``. Optional extra:
``pip install 'meemee[transformers]'``.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .llm import ModelError
from .types import AgentDecision

_CACHE: dict[tuple[str, str], tuple[Any, Any]] = {}
_CACHE_LOCK = threading.Lock()
_GEN_LOCKS: dict[tuple[str, str], threading.Lock] = {}


def transformers_missing() -> str | None:
    """Why the transformers transport cannot run here, or None when it can."""
    missing = [m for m in ("transformers", "torch") if importlib.util.find_spec(m) is None]
    if missing:
        return f"{' and '.join(missing)} not installed; pip install 'meemee[transformers]'"
    return None


def weights_available(model_id: str) -> bool:
    """True when the weights are on disk (a local directory or the HF cache). Never downloads."""
    if Path(model_id).expanduser().is_dir():
        return True
    try:
        from huggingface_hub import try_to_load_from_cache
    except ImportError:
        return False
    found = try_to_load_from_cache(model_id, "config.json")
    return isinstance(found, str)


def pull_weights(model_id: str) -> str:
    """Download a model snapshot into the local HF cache and return its path."""
    if Path(model_id).expanduser().is_dir():
        return str(Path(model_id).expanduser())
    from huggingface_hub import snapshot_download

    return snapshot_download(
        model_id,
        allow_patterns=["*.json", "*.safetensors", "*.model", "*.txt", "*.jinja", "tokenizer*"],
    )


def extract_json_object(text: str) -> str:
    """First balanced top-level JSON object in ``text`` (small models often wrap JSON in prose)."""
    start = text.find("{")
    while start != -1:
        depth, in_str, escape = 0, False, False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start : i + 1]
                    try:
                        json.loads(candidate)
                        return candidate
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)
    raise ModelError("model output contains no JSON object")


def _default_loader(model_id: str, device: str) -> tuple[Any, Any]:
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(model_id, local_files_only=True)
    if device == "auto":
        if torch.cuda.is_available():
            device = "cuda"
        elif getattr(torch.backends, "mps", None) is not None and torch.backends.mps.is_available():
            device = "mps"
        else:
            device = "cpu"
    dtype = torch.float32 if device == "cpu" else torch.float16
    model = AutoModelForCausalLM.from_pretrained(model_id, local_files_only=True, dtype=dtype)
    model.to(device)
    model.eval()
    return tokenizer, model


_REAL_LOADER = _default_loader


def _prompt_ids(tokenizer: Any, messages: list[dict[str, str]]) -> Any:
    if getattr(tokenizer, "chat_template", None):
        encoded = tokenizer.apply_chat_template(
            messages, add_generation_prompt=True, return_tensors="pt", return_dict=True
        )
        return encoded["input_ids"]
    # Base checkpoints without a chat template: a plain role-prefixed transcript.
    text = "".join(f"{m['role']}: {m['content']}\n" for m in messages) + "assistant:"
    return tokenizer(text, return_tensors="pt")["input_ids"]


class TransformersModel:
    """Local open-weight model run in-process with transformers."""

    def __init__(
        self,
        model: str,
        device: str = "auto",
        max_new_tokens: int = 512,
        loader: Callable[[str, str], tuple[Any, Any]] | None = None,
    ):
        if not model:
            raise ValueError("transformers profile needs a model id or local path")
        self.model = model
        self.device = device
        self.max_new_tokens = max_new_tokens
        self._loader = loader or _default_loader
        self._key = (model, device)

    def _load(self) -> tuple[Any, Any]:
        with _CACHE_LOCK:
            if self._key not in _CACHE:
                if self._loader is _REAL_LOADER:
                    reason = transformers_missing()
                    if reason:
                        raise ModelError(reason)
                    if not weights_available(self.model):
                        raise ModelError(
                            f"weights for {self.model!r} are not downloaded; run `meemee models pull` for this profile"
                        )
                try:
                    _CACHE[self._key] = self._loader(self.model, self.device)
                except ModelError:
                    raise
                except Exception as exc:
                    raise ModelError(f"could not load {self.model!r}: {type(exc).__name__}: {exc}") from exc
                _GEN_LOCKS[self._key] = threading.Lock()
            return _CACHE[self._key]

    def _generate(self, messages: list[dict[str, str]], temperature: float, max_tokens: int | None) -> str:
        tokenizer, model = self._load()
        try:
            input_ids = _prompt_ids(tokenizer, messages)
            input_ids = input_ids.to(getattr(model, "device", "cpu"))
            kwargs: dict[str, Any] = {
                "max_new_tokens": max_tokens or self.max_new_tokens,
                "pad_token_id": getattr(tokenizer, "pad_token_id", None) or getattr(tokenizer, "eos_token_id", None),
            }
            if temperature > 0:
                kwargs.update(do_sample=True, temperature=temperature)
            else:
                kwargs.update(do_sample=False)
            with _GEN_LOCKS[self._key]:
                output = model.generate(input_ids, **kwargs)
            new_tokens = output[0][input_ids.shape[-1] :]
            return tokenizer.decode(new_tokens, skip_special_tokens=True)
        except ModelError:
            raise
        except Exception as exc:
            raise ModelError(f"local generation failed: {type(exc).__name__}: {exc}") from exc

    async def chat(self, messages: list[dict[str, str]], temperature: float = 0.7, max_tokens: int | None = None) -> str:
        text = (await asyncio.to_thread(self._generate, messages, temperature, max_tokens)).strip()
        if not text:
            raise ModelError("model returned an empty chat completion")
        return text

    async def decide(self, messages: list[dict[str, str]]) -> AgentDecision:
        text = await asyncio.to_thread(self._generate, messages, 0.0, None)
        try:
            return AgentDecision.model_validate_json(extract_json_object(text))
        except ModelError:
            raise
        except ValueError as exc:
            raise ModelError(f"model returned an invalid decision payload: {exc}") from exc

    async def aclose(self) -> None:
        """Weights stay cached for the process so later requests do not reload them."""


def clear_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()
        _GEN_LOCKS.clear()
