from __future__ import annotations

import asyncio
import json
from typing import Annotated

import typer

from ..config import Settings
from .models import CheckInPreferences, FactInput, PersonaConfig, QuietHours, UserProfile
from .runtime import build_companion
from .worker import checkin_forever

companion_app = typer.Typer(no_args_is_help=True, help="Companion layer: users, persona, chat, facts and check-ins")


@companion_app.command("upsert-user")
def upsert_user(
    user_id: str,
    display_name: str = typer.Option(..., "--display-name"),
    timezone: str = typer.Option("UTC", "--timezone"),
    tone: str = typer.Option("warm, direct and honest", "--tone"),
    language: str = typer.Option("en", "--language"),
    emoji: bool = typer.Option(False, "--emoji"),
    style_rule: Annotated[list[str] | None, typer.Option("--style-rule")] = None,
    instructions: str = typer.Option("", "--instructions"),
) -> None:
    """Create or update a companion user and persona."""
    companion = build_companion(Settings())
    profile = UserProfile(
        user_id=user_id,
        display_name=display_name,
        timezone=timezone,
        persona=PersonaConfig(
            tone=tone, language=language, use_emoji=emoji,
            style_rules=style_rule or [], custom_instructions=instructions,
        ),
        checkins=CheckInPreferences(),
    )
    existing = companion.store.profile(user_id)
    if existing is not None:
        profile.checkins = existing.checkins
        profile.persona.display_name = existing.persona.display_name
    typer.echo(json.dumps(companion.store.upsert_user(profile), indent=2))


@companion_app.command("show-user")
def show_user(user_id: str) -> None:
    """Show one companion profile with persona and check-in preferences."""
    companion = build_companion(Settings())
    record = companion.store.get_user(user_id)
    if record is None:
        raise typer.BadParameter(f"unknown companion user: {user_id}")
    typer.echo(json.dumps(record, indent=2))


@companion_app.command("set-persona")
def set_persona(
    user_id: str,
    persona_name: str = typer.Option(None, "--persona-name"),
    tone: str = typer.Option(None, "--tone"),
    language: str = typer.Option(None, "--language"),
    emoji: bool = typer.Option(None, "--emoji/--no-emoji"),
    style_rule: Annotated[list[str] | None, typer.Option("--style-rule")] = None,
    instructions: str = typer.Option(None, "--instructions"),
) -> None:
    """Update only the persona of an existing companion user."""
    companion = build_companion(Settings())
    profile = companion.store.profile(user_id)
    if profile is None:
        raise typer.BadParameter(f"unknown companion user: {user_id}")
    persona = profile.persona
    if persona_name is not None:
        persona.display_name = persona_name
    if tone is not None:
        persona.tone = tone
    if language is not None:
        persona.language = language
    if emoji is not None:
        persona.use_emoji = emoji
    if style_rule:
        persona.style_rules = style_rule
    if instructions is not None:
        persona.custom_instructions = instructions
    profile.persona = persona
    typer.echo(json.dumps(companion.store.upsert_user(profile), indent=2))


@companion_app.command("set-checkins")
def set_checkins(
    user_id: str,
    enabled: bool = typer.Option(..., "--enabled/--disabled"),
    cadence_minutes: int = typer.Option(360, "--cadence-minutes"),
    quiet_start: str = typer.Option(None, "--quiet-start"),
    quiet_end: str = typer.Option(None, "--quiet-end"),
    channel: str = typer.Option("local", "--channel"),
    address: str = typer.Option(None, "--address"),
) -> None:
    """Configure proactive check-ins for a user."""
    companion = build_companion(Settings())
    profile = companion.store.profile(user_id)
    if profile is None:
        raise typer.BadParameter(f"unknown companion user: {user_id}")
    if (quiet_start is None) != (quiet_end is None):
        raise typer.BadParameter("quiet hours need both --quiet-start and --quiet-end")
    quiet = QuietHours(start=quiet_start, end=quiet_end) if quiet_start else None
    profile.checkins = CheckInPreferences(
        enabled=enabled, cadence_minutes=cadence_minutes, quiet_hours=quiet,
        channel=channel, address=address,
    )
    saved = companion.store.upsert_user(profile)
    if not enabled:
        cancelled = companion.store.cancel_pending_checkins(user_id)
        typer.echo(f"cancelled {cancelled} pending check-ins", err=True)
    typer.echo(json.dumps(saved["checkins"], indent=2))


@companion_app.command("add-fact")
def add_fact(
    user_id: str,
    text: str,
    category: str = typer.Option("general", "--category"),
    confidence: float = typer.Option(1.0, "--confidence"),
) -> None:
    """Record a durable fact about a user."""
    companion = build_companion(Settings())
    if companion.store.get_user(user_id) is None:
        raise typer.BadParameter(f"unknown companion user: {user_id}")
    fact = companion.store.add_fact(user_id, FactInput(category=category, text=text, confidence=confidence), "cli")
    typer.echo(json.dumps(fact, indent=2))


@companion_app.command("facts")
def facts(user_id: str, query: str = typer.Option(None, "--query")) -> None:
    """List or search a user's durable facts."""
    companion = build_companion(Settings())
    if query:
        rows = companion.store.search_facts(user_id, query)
    else:
        rows = companion.store.list_facts(user_id)
    typer.echo(json.dumps(rows, indent=2))


@companion_app.command("chat")
def chat(user_id: str, channel: str = typer.Option("local", "--channel")) -> None:
    """Interactive companion chat on the local channel. Empty line exits."""
    companion = build_companion(Settings())

    async def loop() -> None:
        while True:
            text = typer.prompt("you", default="", show_default=False)
            if not text.strip():
                return
            reply = await companion.engine.reply(user_id, text, channel)
            typer.echo(f"{reply.persona}: {reply.reply}")

    asyncio.run(loop())


@companion_app.command("checkin-worker")
def checkin_worker() -> None:
    """Run the durable proactive check-in loop."""
    asyncio.run(checkin_forever(Settings()))
