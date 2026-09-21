from __future__ import annotations

import asyncio
import json
import random
from typing import Any, Protocol

import httpx

from .types import AgentDecision


class Model(Protocol):
    async def decide(self, messages: list[dict[str, str]]) -> AgentDecision: ...


class ModelError(RuntimeError):
    pass


class OpenAICompatibleModel:
    """Reliable client for Ollama, vLLM, llama.cpp and OpenAI-compatible chat endpoints."""

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str,
        timeout: float = 60.0,
        max_attempts: int = 3,
        client: httpx.AsyncClient | None = None,
    ):
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.max_attempts = max_attempts
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=min(timeout, 10)),
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
        )

    async def decide(self, messages: list[dict[str, str]]) -> AgentDecision:
        last_error: Exception | None = None
        attempts_used = 0
        for attempt in range(1, self.max_attempts + 1):
            attempts_used = attempt
            try:
                response = await self.client.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={
                        "model": self.model,
                        "messages": messages,
                        "temperature": 0.1,
                        "response_format": {"type": "json_object"},
                    },
                )
                if response.status_code in {408, 429, 500, 502, 503, 504}:
                    raise httpx.HTTPStatusError(
                        f"transient model HTTP {response.status_code}",
                        request=response.request,
                        response=response,
                    )
                response.raise_for_status()
                payload: dict[str, Any] = response.json()
                text = payload["choices"][0]["message"]["content"]
                return AgentDecision.model_validate_json(text)
            except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
                last_error = exc
                retryable = not isinstance(exc, httpx.HTTPStatusError) or exc.response.status_code in {
                    408, 429, 500, 502, 503, 504
                }
                if not retryable or attempt == self.max_attempts:
                    break
                retry_after = exc.response.headers.get("retry-after") if isinstance(exc, httpx.HTTPStatusError) else None
                try:
                    delay = min(float(retry_after), 30.0) if retry_after else min(2 ** (attempt - 1), 8) + random.random() * 0.25
                except ValueError:
                    delay = min(2 ** (attempt - 1), 8)
                await asyncio.sleep(delay)
            except (KeyError, IndexError, TypeError, json.JSONDecodeError, ValueError) as exc:
                raise ModelError(f"model returned an invalid decision payload: {exc}") from exc
        raise ModelError(f"model request failed after {attempts_used} attempts: {last_error}") from last_error

    async def aclose(self) -> None:
        await self.client.aclose()
