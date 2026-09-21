from __future__ import annotations

import json
from typing import Any, Protocol

import httpx

from .types import AgentDecision


class Model(Protocol):
    async def decide(self, messages: list[dict[str, str]]) -> AgentDecision: ...


class OpenAICompatibleModel:
    """Client for Ollama, vLLM, llama.cpp and OpenAI-compatible chat endpoints."""

    def __init__(self, base_url: str, model: str, api_key: str, timeout: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.client = httpx.AsyncClient(timeout=timeout)

    async def decide(self, messages: list[dict[str, str]]) -> AgentDecision:
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
        response.raise_for_status()
        payload: dict[str, Any] = response.json()
        text = payload["choices"][0]["message"]["content"]
        try:
            return AgentDecision.model_validate_json(text)
        except (ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f"model returned invalid agent decision: {exc}") from exc

    async def aclose(self) -> None:
        await self.client.aclose()
