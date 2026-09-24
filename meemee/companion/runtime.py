from __future__ import annotations

from dataclasses import dataclass

from ..config import Settings
from ..context import ContextStore
from ..llm import OpenAICompatibleModel
from ..model_profiles import RoutedModel, build_role_model
from ..personal_model import PersonalModelStore
from .channels import build_channels
from .engine import CompanionEngine
from .store import CompanionStore


@dataclass
class Companion:
    store: CompanionStore
    engine: CompanionEngine
    channels: dict
    model: OpenAICompatibleModel | RoutedModel


def build_companion(settings: Settings | None = None, store: CompanionStore | None = None) -> Companion:
    """Compose the companion layer from runtime settings."""
    settings = settings or Settings()
    if store is None and settings.persistence_backend.strip().lower() == "postgresql":
        # Standalone callers (companion worker, CLI): the shared companion tables, not a local file.
        from ..persistence import persistence_from_settings

        store = persistence_from_settings(settings).companion
    companion_store = store or CompanionStore(settings.data_dir / "companion.sqlite3")
    context_store = ContextStore(settings.data_dir / "context.sqlite3")
    personal_model = PersonalModelStore(settings.data_dir / "personal-model.sqlite3")
    model = build_role_model(settings, "chat")
    engine = CompanionEngine(
        model,
        companion_store,
        history_limit=settings.companion_history_limit,
        fact_limit=settings.companion_fact_limit,
        temperature=settings.companion_model_temperature,
        context=context_store,
        personal_model=personal_model,
    )
    channels = build_channels(settings, companion_store)
    return Companion(companion_store, engine, channels, model)
