from pathlib import Path

from fastapi.staticfiles import StaticFiles

WEBAPP_DIR = Path(__file__).resolve().parent


def mount_webapp(app, url_path: str = "/app") -> None:
    """Mount the packaged customer application with HTML directory indexes."""
    app.mount(url_path, StaticFiles(directory=WEBAPP_DIR, html=True), name="webapp")
