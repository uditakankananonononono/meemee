"""Providers. All are free routes: local/self-hosted servers, the HF router on her own
HF_TOKEN (free tier; the router may bill beyond it - see README), and on-device Needle."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

LOCAL, HOSTED = "local", "hosted"


class ProviderError(RuntimeError):
    pass


class ProviderUnavailable(ProviderError):
    """Not configured or not reachable; the router may try the next route."""


@dataclass
class ChatResult:
    provider: str
    model: str
    text: str
    tool_calls: list[dict]
    raw: dict


Transport = Callable[[str, dict, dict, float], dict]


def http_json(url: str, body: dict, headers: dict, timeout: float) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        raise ProviderError(f"HTTP {exc.code} from {url}: {exc.read()[:300]!r}") from exc
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        raise ProviderUnavailable(f"cannot reach {url}: {exc}") from exc


class Provider:
    name = "provider"
    locality = LOCAL

    def available(self) -> bool:
        raise NotImplementedError

    def chat(self, messages: list[dict], *, tools: list[dict] | None = None, max_tokens: int = 1024) -> ChatResult:
        raise NotImplementedError


class _OpenAICompat(Provider):
    def __init__(self, base_url: str | None, model: str | None, api_key: str | None = None,
                 transport: Transport = http_json, timeout: float = 120):
        self.base_url, self.model, self.api_key, self.transport, self.timeout = (base_url or "").rstrip("/"), model, api_key, transport, timeout

    def available(self) -> bool:
        return bool(self.base_url and self.model)

    def chat(self, messages, *, tools=None, max_tokens=1024):
        if not self.available():
            raise ProviderUnavailable(f"{self.name} is not configured")
        body: dict[str, Any] = {"model": self.model, "messages": messages, "max_tokens": max_tokens}
        if tools:
            body["tools"] = [{"type": "function", "function": t} for t in tools]
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        data = self.transport(f"{self.base_url}/chat/completions", body, headers, self.timeout)
        try:
            msg = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(f"{self.name}: unexpected response shape") from exc
        calls = [{"name": c["function"]["name"], "arguments": json.loads(c["function"].get("arguments") or "{}")}
                 for c in (msg.get("tool_calls") or [])]
        return ChatResult(self.name, self.model, msg.get("content") or "", calls, data)


class InklingLocal(_OpenAICompat):
    """Self-hosted Inkling (llama.cpp GGUF / vLLM / SGLang) on her own hardware."""
    name, locality = "inkling-local", LOCAL


class OrnithOpenAICompat(_OpenAICompat):
    """Ornith-1.5 GGUF served locally by llama.cpp or Ollama (OpenAI-compatible /v1)."""
    name, locality = "ornith-local", LOCAL


class InklingHFRouter(_OpenAICompat):
    """Inkling through the Hugging Face router with her HF_TOKEN. Hosted: never used for private tasks."""
    name, locality = "inkling-hf-router", HOSTED

    def __init__(self, model: str, token: str | None = None, transport: Transport = http_json, timeout: float = 120):
        super().__init__("https://router.huggingface.co/v1", model, token if token is not None else os.environ.get("HF_TOKEN"),
                         transport, timeout)

    def available(self) -> bool:
        return bool(self.model and self.api_key)


class NeedleLocal(Provider):
    """On-device Needle (pip cactus-needle). Tool selection + argument extraction only:
    short inputs (256-token window at inference), no free-form generation."""
    name, locality = "needle-local", LOCAL

    def __init__(self, weights: str | None = None, factory: Callable[..., Any] | None = None, min_confidence: float = 0.8,
                 telemetry: bool = False):
        self.weights, self.factory, self.min_confidence = weights, factory, min_confidence
        # cactus-needle sends anonymous usage counts to its developers by default. Off unless opted in.
        self.telemetry = telemetry

    def _factory(self):
        if self.factory:
            return self.factory
        if not self.telemetry:
            os.environ["NEEDLE_TELEMETRY"] = "0"
        try:
            import needle  # type: ignore  # pip install cactus-needle; import does not load JAX
        except ImportError as exc:
            raise ProviderUnavailable("cactus-needle is not installed (pip install cactus-needle)") from exc
        return needle.Needle

    def available(self) -> bool:
        try:
            self._factory()
            return True
        except ProviderUnavailable:
            return False

    def chat(self, messages, *, tools=None, max_tokens=256):
        """One turn via ``agent.complete`` (documented API). Returns calls only when
        the model called a tool; tuned weights report no calibrated confidence, so the
        threshold applies to the base model only."""
        if not tools:
            raise ProviderUnavailable("Needle only handles tool-calling turns")
        query = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
        system = next((m["content"] for m in messages if m.get("role") == "system"), None)
        kwargs: dict[str, Any] = {"tools": tools}
        if self.weights:
            kwargs["weights"] = self.weights
        if system:
            kwargs["system"] = system
        agent = self._factory()(**kwargs)
        out = agent.complete(query, max_new_tokens=min(max_tokens, 256))
        if not isinstance(out, dict) or not out.get("success", True):
            raise ProviderError(f"needle failed: {out.get('error') if isinstance(out, dict) else out!r}")
        calls = list(out.get("function_calls") or []) if out.get("type") == "call" else []
        conf = out.get("confidence")
        if calls and not self.weights and isinstance(conf, (int, float)) and conf < self.min_confidence:
            calls = []  # low confidence: let the router escalate
        if calls and (out.get("validation") or {}).get("ungrounded"):
            calls = []  # Needle flagged argument values not found in the query: escalate instead of trusting them
        return ChatResult(self.name, self.weights or "needle-base", "", calls, out)
