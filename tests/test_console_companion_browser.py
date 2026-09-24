"""Headless Chromium run of the console companion screens against a live API server."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

playwright_api = pytest.importorskip("playwright.sync_api")

TOKEN = "console-browser-test-bootstrap-token"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _seed(data_dir: Path) -> None:
    from meemee.companion.models import CheckInPreferences, FactInput, PersonaConfig, UserProfile
    from meemee.companion.store import CompanionStore

    store = CompanionStore(data_dir / "companion.sqlite3")
    persona = PersonaConfig(display_name="Mee", tone="dry and brief", custom_instructions="keep persona intact")
    store.upsert_user(UserProfile(user_id="ada", display_name="Ada", timezone="UTC", persona=persona,
                                  checkins=CheckInPreferences(enabled=True, cadence_minutes=1440)))
    store.add_fact("ada", FactInput(text="prefers tea over coffee", category="preference"), source="seed")
    local = store.start_conversation("ada", "local")
    store.add_message(local["id"], "user", "hello from the local channel")
    store.add_message(local["id"], "assistant", "local reply")
    whatsapp = store.start_conversation("ada", "whatsapp")
    store.add_message(whatsapp["id"], "user", "whatsapp-only message about the ferry")
    future = datetime.now(timezone.utc) + timedelta(days=2)
    store.schedule_checkin("ada", future, "slot-queued", "local", None)
    failed, _ = store.schedule_checkin("ada", datetime.now(timezone.utc) - timedelta(minutes=5), "slot-failed", "local", None, max_attempts=1)
    claimed = store.claim_checkin()
    assert claimed and claimed["id"] == failed["id"]
    store.fail_checkin(failed["id"], "provider unreachable")


@pytest.fixture()
def server(tmp_path):
    from meemee.vault import SecretVault

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    _seed(data_dir)
    port = _free_port()
    env = {**os.environ, "MEEMEE_DATA_DIR": str(data_dir), "MEEMEE_API_TOKEN": TOKEN,
           "MEEMEE_VAULT_KEY": SecretVault.generate_key(), "MEEMEE_TRUSTED_HOSTS": "127.0.0.1,localhost",
           "MEEMEE_WORKSPACE": str(tmp_path)}
    process = subprocess.Popen([sys.executable, "-m", "uvicorn", "meemee.api:app", "--host", "127.0.0.1", "--port", str(port)],
                               env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            if httpx.get(f"{base}/health", timeout=0.5).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        if process.poll() is not None:
            raise RuntimeError(process.stdout.read().decode())
        time.sleep(0.1)
    yield base, data_dir
    process.terminate()
    process.wait(timeout=10)


def _api(base: str, path: str):
    return httpx.get(f"{base}{path}", headers={"Authorization": f"Bearer {TOKEN}"}, timeout=5).json()


def test_companion_console_screens_in_headless_chromium(server, tmp_path):
    base, _ = server
    with playwright_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # browser binary not installed in this environment
            pytest.skip(f"chromium unavailable: {exc}")
        page = browser.new_page(viewport={"width": 1280, "height": 1800})
        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.on("dialog", lambda dialog: dialog.accept())
        page.add_init_script(f"window.sessionStorage.setItem('meemee.console.token', {json.dumps(TOKEN)})")
        page.goto(f"{base}/console/#/companion")
        page.get_by_role("button", name="Ada (ada)").click()

        # Profile editor: rename + timezone, persona must survive the full-profile PUT.
        profile = page.get_by_test_id("companion-profile")
        profile.locator("input[name=profile-display-name]").fill("Ada Lovelace")
        profile.locator("input[name=profile-timezone]").fill("Asia/Calcutta")
        profile.get_by_test_id("save-profile").click()
        page.get_by_role("button", name="Ada Lovelace (ada)").wait_for()
        user = _api(base, "/v1/companion/users/ada")
        assert user["display_name"] == "Ada Lovelace" and user["timezone"] == "Asia/Calcutta"
        assert user["persona"]["tone"] == "dry and brief" and user["persona"]["custom_instructions"] == "keep persona intact"
        assert user["checkins"]["enabled"] is True  # saving the profile must not switch check-ins off

        # Facts: add, then delete (retire) from the list.
        page.get_by_placeholder("new fact text").fill("allergic to peanuts")
        page.get_by_role("button", name="Add fact").click()
        row = page.locator("tr", has_text="allergic to peanuts")
        row.wait_for()
        row.get_by_role("button", name="retire").click()
        row.wait_for(state="detached")
        facts = [f["text"] for f in _api(base, "/v1/companion/users/ada/facts")["facts"]]
        assert "allergic to peanuts" not in facts and "prefers tea over coffee" in facts

        # Conversations: both channels listed; opening the WhatsApp one shows its history.
        conversations = page.get_by_test_id("companion-conversations")
        buttons = conversations.locator("button[data-conversation-id]")
        assert buttons.count() == 2
        conversations.get_by_role("button", name="whatsapp", exact=False).click()
        log = page.get_by_test_id("conversation-log")
        log.get_by_text("whatsapp-only message about the ferry").wait_for()
        assert "local reply" not in log.inner_text()
        conversations.get_by_role("button", name="local", exact=False).click()
        log.get_by_text("local reply").wait_for()

        # Check-in queue: status filter and counts.
        queue = page.get_by_test_id("checkin-queue")
        queue.get_by_text("provider unreachable").wait_for()
        page.locator("select[name=checkin-status]").select_option("failed")
        page.get_by_test_id("checkin-counts").get_by_text("failed: 1").wait_for()
        assert "queued" not in page.get_by_test_id("checkin-counts").inner_text()
        page.locator("select[name=checkin-status]").select_option("")
        page.get_by_test_id("checkin-counts").get_by_text("queued").wait_for()

        # Admin delivery run over the queue (nothing due, so it completes without a model).
        page.get_by_test_id("deliver-due").click()
        page.get_by_text("processed 0 due check-in(s)").wait_for()
        statuses = [c["status"] for c in _api(base, "/v1/companion/users/ada/checkins")["checkins"]]
        assert "queued" in statuses and "failed" in statuses and "cancelled" not in statuses

        page.screenshot(path=str(tmp_path / "companion-console.png"), full_page=True)
        browser.close()
    assert errors == []
