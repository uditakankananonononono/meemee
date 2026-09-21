from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


class BackupManager:
    """Consistent online SQLite backup with integrity check and SHA-256 manifest."""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir.resolve()

    def create(self, destination: Path) -> dict[str, object]:
        destination.mkdir(parents=True, exist_ok=False)
        files: list[dict[str, object]] = []
        for source in sorted(self.data_dir.glob("*.sqlite3")):
            target = destination / source.name
            source_db = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
            target_db = sqlite3.connect(target)
            try:
                source_db.backup(target_db)
                integrity = target_db.execute("PRAGMA integrity_check").fetchone()[0]
                if integrity != "ok":
                    raise RuntimeError(f"backup integrity failed for {source.name}: {integrity}")
            finally:
                source_db.close(); target_db.close()
            digest = hashlib.sha256(target.read_bytes()).hexdigest()
            files.append({"name": source.name, "bytes": target.stat().st_size, "sha256": digest})
        manifest = {
            "format": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source": str(self.data_dir),
            "files": files,
        }
        encoded = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
        (destination / "manifest.json").write_text(encoded)
        return manifest

    @staticmethod
    def verify(directory: Path) -> dict[str, object]:
        manifest = json.loads((directory / "manifest.json").read_text())
        for expected in manifest["files"]:
            path = directory / expected["name"]
            if not path.is_file() or path.stat().st_size != expected["bytes"]:
                raise RuntimeError(f"backup file missing or wrong size: {expected['name']}")
            if hashlib.sha256(path.read_bytes()).hexdigest() != expected["sha256"]:
                raise RuntimeError(f"backup checksum mismatch: {expected['name']}")
            db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            try:
                if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise RuntimeError(f"backup database corrupt: {expected['name']}")
            finally:
                db.close()
        return manifest
