"""Needle-first router with escalation, privacy-aware.

- Tool-calling tasks try Needle first (tiny, on-device); if Needle returns no call
  or errors, escalate to Ornith (local) then Inkling local, then the HF router.
- Generation tasks skip Needle (it does not generate prose).
- private=True never reaches a hosted route: the chain stops instead.
- No paid route exists in this package.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .config import ProductConfig
from .providers import (HOSTED, ChatResult, InklingHFRouter, InklingLocal, NeedleLocal, OrnithOpenAICompat, HermesLocal, Provider,
                        ProviderError)


@dataclass
class Task:
    messages: list[dict]
    tools: list[dict] | None = None
    private: bool = False
    max_tokens: int = 1024


@dataclass
class RouteAttempt:
    provider: str
    outcome: str
    detail: str = ""


@dataclass
class RoutedResult:
    result: ChatResult | None
    attempts: list[RouteAttempt] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.result is not None


class Router:
    def __init__(self, providers: list[Provider]):
        self.providers = providers

    @classmethod
    def from_config(cls, cfg: ProductConfig) -> "Router":
        chain: list[Provider] = [NeedleLocal(cfg.needle_weights),
                                 OrnithOpenAICompat(cfg.ornith_url, cfg.ornith_model),
                                 InklingLocal(cfg.inkling_local_url, cfg.inkling_local_model)]
        if cfg.hermes_url and cfg.hermes_model:
            chain.append(HermesLocal(cfg.hermes_url, cfg.hermes_model))
        if cfg.allow_hosted:
            chain.append(InklingHFRouter(cfg.hf_model))
        return cls(chain)

    def run(self, task: Task) -> RoutedResult:
        out = RoutedResult(None)
        for p in self.providers:
            if isinstance(p, NeedleLocal) and not task.tools:
                out.attempts.append(RouteAttempt(p.name, "skipped", "not a tool-calling task"))
                continue
            if p.locality == HOSTED and task.private:
                out.attempts.append(RouteAttempt(p.name, "skipped", "private task never goes to a hosted route"))
                continue
            if not p.available():
                out.attempts.append(RouteAttempt(p.name, "unavailable"))
                continue
            try:
                res = p.chat(task.messages, tools=task.tools, max_tokens=task.max_tokens)
            except ProviderError as exc:
                out.attempts.append(RouteAttempt(p.name, "error", str(exc)[:300]))
                continue
            if task.tools and not res.tool_calls and isinstance(p, NeedleLocal):
                out.attempts.append(RouteAttempt(p.name, "escalated", "no tool call"))
                continue
            out.attempts.append(RouteAttempt(p.name, "ok"))
            out.result = res
            return out
        return out
