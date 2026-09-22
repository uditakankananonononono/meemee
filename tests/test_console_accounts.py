from pathlib import Path


def test_console_ships_account_plan_and_quota_administration():
    root=Path(__file__).parents[1]
    app=(root/"console/assets/app.js").read_text(); view=(root/"console/assets/views/accounts.js").read_text(); api=(root/"console/assets/api.js").read_text()
    assert "renderAccounts" in app and 'title: "Accounts"' in app
    assert "assignPrincipalPlan" in api and "setPrincipalQuota" in api
    assert "window.confirm" in view and "identity provider" in view and "Daily jobs must be an integer" in view
