from __future__ import annotations

import re
import zipfile
from pathlib import Path, PurePosixPath

REQUIRED_WHEEL_PATHS = (
    "console/__init__.py", "console/mount.py", "console/index.html",
    "console/assets/app.js", "console/assets/app.css", "meemee/api.py",
    "meemee/cli.py", "meemee/py.typed", "meemee_persist_pg/__init__.py",
    "meemee_persist_pg/sql/001_initial.sql", "meemee_persist_pg/sql/002_job_ownership.sql",
)


def audit_wheel(wheel: Path, expected_version: str) -> dict:
    """Inspect a built wheel without installing or executing it."""
    findings: list[dict] = []
    if not wheel.is_file() or wheel.suffix != ".whl":
        return {"status":"fail", "findings":[{"code":"wheel_missing","path":str(wheel)}]}
    try:
        with zipfile.ZipFile(wheel) as archive:
            members = archive.infolist(); names = [member.filename for member in members]
            if len(names) != len(set(names)):
                findings.append({"code":"wheel_duplicate_member"})
            for name in names:
                path = PurePosixPath(name)
                if path.is_absolute() or ".." in path.parts or "\\" in name:
                    findings.append({"code":"wheel_unsafe_path","path":name})
            corrupt = archive.testzip()
            if corrupt is not None:
                findings.append({"code":"wheel_crc_failure","path":corrupt})
            unique = set(names)
            for path in REQUIRED_WHEEL_PATHS:
                if path not in unique:
                    findings.append({"code":"wheel_content_missing","path":path})
            metadata_names = [name for name in unique if name.endswith(".dist-info/METADATA")]
            entry_names = [name for name in unique if name.endswith(".dist-info/entry_points.txt")]
            record_names = [name for name in unique if name.endswith(".dist-info/RECORD")]
            if len(metadata_names) != 1:
                findings.append({"code":"wheel_metadata_invalid"})
            else:
                metadata = archive.read(metadata_names[0]).decode(errors="replace")
                name_match = re.search(r"^Name: (.+)$", metadata, re.MULTILINE)
                version_match = re.search(r"^Version: (.+)$", metadata, re.MULTILINE)
                license_match = re.search(r"^License-Expression: (.+)$", metadata, re.MULTILINE)
                if not name_match or name_match.group(1) != "meemee-agent":
                    findings.append({"code":"wheel_distribution_mismatch"})
                if not version_match or version_match.group(1) != expected_version:
                    findings.append({"code":"wheel_version_mismatch","expected":expected_version})
                if not license_match or license_match.group(1) != "LicenseRef-Proprietary":
                    findings.append({"code":"wheel_license_expression_invalid"})
            license_names = [name for name in unique if name.endswith(".dist-info/licenses/LICENSE")]
            if len(license_names) != 1:
                findings.append({"code":"wheel_license_missing"})
            else:
                license_text = archive.read(license_names[0]).decode(errors="replace")
                if "All rights reserved" not in license_text or "proprietary and confidential" not in license_text:
                    findings.append({"code":"wheel_license_invalid"})
            if len(entry_names) != 1 or "meemee = meemee.cli:app" not in archive.read(entry_names[0]).decode(errors="replace"):
                findings.append({"code":"wheel_cli_entry_missing"})
            if len(record_names) != 1:
                findings.append({"code":"wheel_record_missing"})
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        findings.append({"code":"wheel_archive_invalid","detail":str(exc)})
    return {"status":"pass" if not findings else "fail","findings":findings,"summary":{"findings":len(findings)}}
