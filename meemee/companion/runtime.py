from __future__ import annotations

from dataclasses import dataclass

from ..config import Settings
from ..context import ContextStore
from ..llm import OpenAICompatibleModel
from .channels import build_channels
from .engine import CompanionEngine
from .store import CompanionStore


@dataclass
class Companion:
    store: CompanionStore
    engine: CompanionEngine
    channels: dict
    model: OpenAICompatibleModel


def build_companion(settings: Settings | None = None, store: CompanionStore | None = None) -> Companion:
    """Compose the companion layer from runtime settings."""
    settings = settings or Settings()
    companion_store = store or CompanionStore(settings.data_dir / "companion.sqlite3")
    context_store = ContextStore(settings.data_dir / "context.sqlite3")
    model = OpenAICompatibleModel(
        settings.model_base_url,
        settings.model_name,
        settings.model_api_key,
        settings.request_timeout,
        settings.model_max_attempts,
    )
    engine = CompanionEngine(
        model,
        companion_store,
        history_limit=settings.companion_history_limit,
        fact_limit=settings.companion_fact_limit,
        temperature=settings.companion_model_temperature,
        context=context_store,
    )
    channels = build_channels(settings, companion_store)
    return Companion(companion_store, engine, channels, model)
