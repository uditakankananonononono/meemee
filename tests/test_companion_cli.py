import json

from typer.testing import CliRunner

from meemee.cli import app

runner = CliRunner()


def test_user_fact_checkin_flow(tmp_path, monkeypatch):
    monkeypatch.setenv("MEEMEE_DATA_DIR", str(tmp_path))
    result = runner.invoke(app, [
        "companion", "upsert-user", "udita", "--display-name", "Udita",
        "--timezone", "Asia/Calcutta", "--tone", "blunt", "--style-rule", "no fluff",
    ])
    assert result.exit_code == 0, result.output
    record = json.loads(result.output)
    assert record["persona"]["tone"] == "blunt"
    assert record["persona"]["style_rules"] == ["no fluff"]

    shown = runner.invoke(app, ["companion", "show-user", "udita"])
    assert shown.exit_code == 0 and json.loads(shown.output)["timezone"] == "Asia/Calcutta"

    persona = runner.invoke(app, ["companion", "set-persona", "udita", "--tone", "gentle", "--emoji"])
    assert persona.exit_code == 0
    assert json.loads(persona.output)["persona"]["use_emoji"] is True

    fact = runner.invoke(app, ["companion", "add-fact", "udita", "Builds the Atlas platform", "--category", "project"])
    assert fact.exit_code == 0 and json.loads(fact.output)["category"] == "project"

    listed = runner.invoke(app, ["companion", "facts", "udita", "--query", "Atlas"])
    assert listed.exit_code == 0 and json.loads(listed.output)[0]["text"].startswith("Builds")

    checkins = runner.invoke(app, [
        "companion", "set-checkins", "udita", "--enabled", "--cadence-minutes", "60",
        "--quiet-start", "22:00", "--quiet-end", "06:00",
    ])
    assert checkins.exit_code == 0
    saved = json.loads(checkins.output)
    assert saved["enabled"] is True and saved["quiet_hours"]["end"] == "06:00"

    off = runner.invoke(app, ["companion", "set-checkins", "udita", "--disabled"])
    assert off.exit_code == 0
    payload = off.output[off.output.index("{"):]
    assert json.loads(payload)["enabled"] is False


def test_unknown_user_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("MEEMEE_DATA_DIR", str(tmp_path))
    result = runner.invoke(app, ["companion", "show-user", "ghost"])
    assert result.exit_code != 0


def test_quiet_hours_need_both_bounds(tmp_path, monkeypatch):
    monkeypatch.setenv("MEEMEE_DATA_DIR", str(tmp_path))
    runner.invoke(app, ["companion", "upsert-user", "u", "--display-name", "U"])
    result = runner.invoke(app, ["companion", "set-checkins", "u", "--enabled", "--quiet-start", "22:00"])
    assert result.exit_code != 0
