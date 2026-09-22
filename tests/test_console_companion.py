from pathlib import Path


def test_console_ships_companion_view_and_api_bindings():
    root = Path(__file__).parents[1]
    app = (root / "console/assets/app.js").read_text()
    api = (root / "console/assets/api.js").read_text()
    view = (root / "console/assets/views/companion.js").read_text()
    assert "renderCompanion" in app and 'title: "Companion"' in app
    for binding in (
        "listCompanionUsers", "upsertCompanionUser", "updateCompanionPersona",
        "updateCompanionCheckins", "listCompanionFacts", "addCompanionFact",
        "retireCompanionFact", "companionChat", "listCompanionConversations",
        "getCompanionMessages", "planCompanionCheckin", "listCompanionCheckins",
    ):
        assert binding in api and binding in view
    assert "/v1/companion/chat" in api
    assert "window.confirm" in view


def test_console_scopes_and_probes_cover_companion():
    root = Path(__file__).parents[1]
    api = (root / "console/assets/api.js").read_text()
    tokens = (root / "console/assets/views/tokens.js").read_text()
    app = (root / "console/assets/app.js").read_text()
    assert "companionRead" in api and "companionWrite" in api
    assert "companion:read" in tokens and "companion:write" in tokens
    assert "companion:read" in app and "companion:write" in app
