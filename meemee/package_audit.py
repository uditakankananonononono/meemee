from __future__ import annotations

import re
import zipfile
from pathlib import Path

REQUIRED_WHEEL_PATHS = (
    "console/__init__.py",
    "console/mount.py",
    "console/index.html",
    "console/assets/app.js",
    "console/assets/app.css",
    "meemee/api.py",
    "meemee/cli.py",
    "meemee/py.typed",
    "meemee_persist_pg/__init__.py",
    "meemee_persist_pg/sql/001_initial.sql",
    "meemee_persist_pg/sql/002_job_ownership.sql",
)


def audit_wheel(wheel: Path, expected_version: str) -> dict:
    """Inspect a built wheel without installing or executing it."""
    findings: list[dict] = []
    if not wheel.is_file() or wheel.suffix != ".whl":
        return {"status":"fail", "findings":[{"code":"wheel_missing","path":str(wheel)}]}
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        for path in REQUIRED_WHEEL_PATHS:
            if path not in names:
                findings.append({"code":"wheel_content_missing","path":path})
        metadata_names = [name for name in names if name.endswith(".dist-info/METADATA")]
        entry_names = [name for name in names if name.endswith(".dist-info/entry_points.txt")]
        if len(metadata_names) != 1:
            findings.append({"code":"wheel_metadata_invalid"})
        else:
            metadata = archive.read(metadata_names[0]).decode(errors="replace")
            match = re.search(r"^Version: (.+)$", metadata, re.MULTILINE)
            if not match or match.group(1) != expected_version:
                findings.append({"code":"wheel_version_mismatch", "expected":expected_version})
            license_match = re.search(r"^License-Expression: (.+)$", metadata, re.MULTILINE)
            if not license_match or license_match.group(1) != "LicenseRef-Proprietary":
                findings.append({"code":"wheel_license_expression_invalid"})
        license_names = [name for name in names if name.endswith(".dist-info/licenses/LICENSE")]
        if len(license_names) != 1:
            findings.append({"code":"wheel_license_missing"})
        else:
            license_text = archive.read(license_names[0]).decode(errors="replace")
            if "All rights reserved" not in license_text or "proprietary and confidential" not in license_text:
                findings.append({"code":"wheel_license_invalid"})
        if len(entry_names) != 1 or "meemee = meemee.cli:app" not in archive.read(entry_names[0]).decode(errors="replace"):
            findings.append({"code":"wheel_cli_entry_missing"})
    return {"status":"pass" if not findings else "fail", "findings":findings, "summary":{"findings":len(findings)}}
