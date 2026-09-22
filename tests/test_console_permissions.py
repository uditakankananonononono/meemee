from pathlib import Path


def test_console_ships_permissions_admin_view_and_api_client():
    root=Path(__file__).parents[1]
    app=(root/"console/assets/app.js").read_text()
    view=(root/"console/assets/views/permissions.js").read_text()
    api=(root/"console/assets/api.js").read_text()
    assert "renderPermissions" in app and 'title: "Permissions"' in app
    assert "argument_constraints" in api and "listApprovals" in api and "revokeApproval" in api
    assert "window.confirm" in view and "Constraints must be a JSON object" in view
