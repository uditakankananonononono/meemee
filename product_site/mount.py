from pathlib import Path

from fastapi.staticfiles import StaticFiles

SITE_DIR = Path(__file__).resolve().parent

def mount_site(app, url_path: str = "/product") -> None:
    app.mount(url_path, StaticFiles(directory=SITE_DIR, html=True), name="product-site")
