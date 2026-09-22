import pytest

from meemee.companion.models import (
    CheckInPreferences,
    FactInput,
    PersonaConfig,
    QuietHours,
    UserProfile,
)


def test_profile_defaults_are_valid():
    profile = UserProfile(user_id="udita", display_name="Udita")
    assert profile.timezone == "UTC"
    assert profile.persona.display_name == "Meemee"
    assert profile.checkins.enabled is False


def test_user_id_pattern_enforced():
    with pytest.raises(ValueError):
        UserProfile(user_id="bad id with spaces", display_name="X")
    with pytest.raises(ValueError):
        UserProfile(user_id="", display_name="X")
    assert UserProfile(user_id="udita.phookan-1_ok", display_name="X").user_id


def test_timezone_must_be_real():
    with pytest.raises(ValueError, match="unknown IANA timezone"):
        UserProfile(user_id="u", display_name="X", timezone="Mars/Olympus")
    assert UserProfile(user_id="u", display_name="X", timezone="Asia/Calcutta").tz().key == "Asia/Calcutta"


def test_quiet_hours_format():
    with pytest.raises(ValueError):
        QuietHours(start="25:00", end="06:00")
    with pytest.raises(ValueError):
        QuietHours(start="22:00", end="6pm")
    assert QuietHours(start="22:00", end="06:30").end == "06:30"


def test_persona_style_rules_cleaned_and_bounded():
    persona = PersonaConfig(style_rules=["  keep it short ", "", "no jargon"])
    assert persona.style_rules == ["keep it short", "no jargon"]
    with pytest.raises(ValueError):
        PersonaConfig(style_rules=["x" * 301])
    with pytest.raises(ValueError):
        PersonaConfig(style_rules=["ok"] * 21)


def test_fact_input_bounds():
    with pytest.raises(ValueError):
        FactInput(text="")
    with pytest.raises(ValueError):
        FactInput(text="x", confidence=1.5)
    assert FactInput(text="likes tea").confidence == 1.0


def test_checkin_cadence_bounds():
    with pytest.raises(ValueError):
        CheckInPreferences(cadence_minutes=5)
    with pytest.raises(ValueError):
        CheckInPreferences(cadence_minutes=20000)
