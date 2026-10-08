from __future__ import annotations

from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any

from ..composition import run_composition
from ..config import Settings
from ..context import ContextStore
from ..llm import OpenAICompatibleModel
from ..model_profiles import RoutedModel, build_role_model
from ..personal_model import PersonalModelStore
from .channels import build_channels, build_channels_async
from .engine import CompanionEngine
from .store import CompanionStore


@dataclass
class Companion:
    store: CompanionStore
    engine: CompanionEngine
    channels: dict
    model: OpenAICompatibleModel | RoutedModel
    owned_persistence: Any = None


async def build_companion_async(settings: Settings | None = None, store: CompanionStore | None = None, persistence: Any = None) -> Companion:
    """Compose the companion layer from runtime settings."""
    settings = settings or Settings()
    async with AsyncExitStack() as cleanup:
        shared = persistence
        owned_persistence = None
        if shared is None and settings.persistence_backend.strip().lower() == "postgresql":
            # PostgreSQL mode: shared companion, context and personal-model tables, not local files.
            from ..persistence import persistence_from_settings

            shared = persistence_from_settings(settings)
            owned_persistence = shared
            cleanup.callback(shared.close)
            store = store or shared.companion
        companion_store = store or CompanionStore(settings.data_dir / "companion.sqlite3")
        context_store = shared.context if shared else ContextStore(settings.data_dir / "context.sqlite3")
        personal_model = shared.personal_model if shared else PersonalModelStore(settings.data_dir / "personal-model.sqlite3")
        model = build_role_model(settings, "chat")
        cleanup.push_async_callback(model.aclose)
        engine = CompanionEngine(
            model,
            companion_store,
            history_limit=settings.companion_history_limit,
            fact_limit=settings.companion_fact_limit,
            temperature=settings.companion_model_temperature,
            context=context_store,
            personal_model=personal_model,
        )
        channels = await build_channels_async(settings, companion_store)
        for channel in channels.values():
            close = getattr(channel, "aclose", None)
            if close is not None:
                cleanup.push_async_callback(close)
        value = Companion(companion_store, engine, channels, model, owned_persistence)
        cleanup.pop_all()
        return value


def build_companion_sync(settings: Settings | None = None, store: CompanionStore | None = None, persistence: Any = None) -> Companion:
    return run_composition(lambda: build_companion_async(settings, store, persistence))


# Legacy composition retained for API import compatibility until awaited bootstrap.
def build_companion(settings: Settings | None = None, store: CompanionStore | None = None, persistence: Any = None) -> Companion:
    """Compose the companion layer from runtime settings."""
    settings = settings or Settings()
    shared = persistence
    owned_persistence = None
    if shared is None and settings.persistence_backend.strip().lower() == "postgresql":
        # PostgreSQL mode: shared companion, context and personal-model tables, not local files.
        from ..persistence import persistence_from_settings

        shared = persistence_from_settings(settings)
        owned_persistence = shared
        store = store or shared.companion
    try:
        companion_store = store or CompanionStore(settings.data_dir / "companion.sqlite3")
        context_store = shared.context if shared else ContextStore(settings.data_dir / "context.sqlite3")
        personal_model = shared.personal_model if shared else PersonalModelStore(settings.data_dir / "personal-model.sqlite3")
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
        return Companion(companion_store, engine, channels, model, owned_persistence)
    except BaseException:
        if owned_persistence is not None:
            owned_persistence.close()
        raise
