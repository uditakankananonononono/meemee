"""Boot several Meemee API servers that share only a PostgreSQL database (one data dir each)."""
from __future__ import annotations

import json
import os
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

from test_live_runs_e2e import ADMIN, MODEL_KEY, REPO_ROOT, ScriptedProvider, _boot, _pg_database


class _Capture(BaseHTTPRequestHandler):
    """Stands in for the Resend API: records every POST /emails body and returns an id."""

    sent: ClassVar[list[dict]] = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))) or b"{}")
        type(self).sent.append({"path": self.path, "auth": self.headers.get("authorization"), "body": body})
        out = json.dumps({"id": f"email_{len(type(self).sent)}"}).encode()
        self.send_response(200); self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(out))); self.end_headers(); self.wfile.write(out)

    def log_message(self, *args):
        pass


@contextmanager
def mail_capture():
    handler = type("Capture", (_Capture,), {"sent": []})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", handler.sent
    finally:
        server.shutdown()


@contextmanager
def pg_hosts(tmp_path_factory, names=("host-a", "host-b"), extra_env: dict | None = None):
    provider_server = ThreadingHTTPServer(("127.0.0.1", 0), ScriptedProvider)
    threading.Thread(target=provider_server.serve_forever, daemon=True).start()
    provider = f"http://127.0.0.1:{provider_server.server_address[1]}/v1"
    dsn, drop = _pg_database()
    workspace = tmp_path_factory.mktemp("mh-ws")
    procs, bases, dirs = [], [], []
    try:
        for name in names:
            data_dir = tmp_path_factory.mktemp(name)
            env = {**os.environ, "PYTHONPATH": str(REPO_ROOT), "MEEMEE_API_TOKEN": ADMIN, "MEEMEE_DATA_DIR": str(data_dir),
                   "MEEMEE_WORKSPACE": str(workspace), "MEEMEE_MODEL_BASE_URL": provider, "MEEMEE_MODEL_NAME": "scripted-e2e",
                   "MEEMEE_MODEL_API_KEY": MODEL_KEY, "MEEMEE_MODEL_MAX_ATTEMPTS": "1",
                   "MEEMEE_VAULT_KEY": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=", "MEEMEE_PERSISTENCE_BACKEND": "postgresql",
                   "MEEMEE_POSTGRES_DSN": dsn, "MEEMEE_RATE_LIMIT_REQUESTS": "10000", **(extra_env or {})}
            for key in ("MEEMEE_MODEL_ROUTES", "MEEMEE_MODEL_PROFILES", "MEEMEE_HF_TOKEN"):
                env.pop(key, None)
            proc, base = _boot(env, workspace, data_dir / "server.log")
            procs.append(proc); bases.append(base); dirs.append(data_dir)
        yield {"bases": bases, "dirs": dirs, "dsn": dsn}
    finally:
        for proc in procs:
            proc.kill(); proc.wait(timeout=10)
        provider_server.shutdown()
        drop()
