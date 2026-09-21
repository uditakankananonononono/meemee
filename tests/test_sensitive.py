from pathlib import Path

import pytest

from meemee.agent import Agent
from meemee.memory import MemoryStore
from meemee.sensitive import scrub_text
from meemee.tools.base import ToolRegistry
from meemee.types import AgentDecision


@pytest.mark.parametrize("secret", [
    "github_pat_abcdefghijklmnopqrstuvwxyz123456",
    "ghp_abcdefghijklmnopqrstuvwxyz123456",
    "sk-abcdefghijklmnopqrstuvwxyz123456",
    "Bearer abcdefghijklmnopqrstuvwxyz.123456",
])
def test_scrub_common_tokens(secret):
    cleaned = scrub_text(f"use {secret} now")
    assert secret not in cleaned and "[REDACTED:" in cleaned


def test_scrub_assignment_preserves_key_not_value():
    cleaned = scrub_text("password=hunter-hunter-123")
    assert cleaned.startswith("password=[REDACTED:")
    assert "hunter" not in cleaned


class CaptureModel:
    def __init__(self): self.messages = None
    async def decide(self, messages): self.messages = messages; return AgentDecision(final="ok")


@pytest.mark.asyncio
async def test_agent_never_passes_or_persists_raw_goal_secret(tmp_path: Path):
    model = CaptureModel(); memory = MemoryStore(tmp_path / "m.db")
    secret = "ghp_abcdefghijklmnopqrstuvwxyz123456"
    report = await Agent(model, ToolRegistry(), memory).run(f"use {secret}")
    assert secret not in report.goal
    assert secret not in str(model.messages)
    assert secret not in str(memory.recent())
