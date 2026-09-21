"""Additive mount helper for the Meemee operator console (console/).

This file is the only Python in the console scope. It is purely additive: it
edits nothing and is imported by nobody unless the main builder wires it.

Wiring for the main builder (one of two options):

Option A - two lines in meemee/api.py, no import of this module:

    from fastapi.staticfiles import StaticFiles
    from pathlib import Path

    app.mount(
        "/console",
        StaticFiles(directory=Path(__file__).resolve().parent.parent / "console", html=True),
        name="console",
    )

Option B - if the repository root is on sys.path (e.g. meemee serve run from
the repo checkout):

    from console.mount import mount_console
    mount_console(app)

Both serve the console at /console/ with index.html as the directory index.
The console talks to the same API origin (session cookie or bearer token),
so no CORS configuration is needed. Note that the interactive OIDC callback
redirects to "/" (meemee/web_login.py); operators land on the minimal home
page after sign-in and navigate to /console/ from there. Changing that
redirect target is a core-file decision owned by the main builder.
"""

from pathlib import Path

from fastapi.staticfiles import StaticFiles

CONSOLE_DIR = Path(__file__).resolve().parent


def mount_console(app, url_path: str = "/console") -> None:
    """Mount the static operator console on an existing FastAPI app."""
    app.mount(url_path, StaticFiles(directory=CONSOLE_DIR, html=True), name="console")
