from pathlib import Path


def test_console_uses_owner_scoped_server_job_listing():
    root = Path(__file__).parents[1]
    view = (root / "console/assets/views/jobs.js").read_text()
    api = (root / "console/assets/api.js").read_text()
    assert "await api.listJobs()" in view
    assert "Account jobs" in view and "loaded from the server" in view
    assert "No job listing endpoint" not in view
    assert "export const listJobs" in api and 'request(`/v1/jobs?' in api
