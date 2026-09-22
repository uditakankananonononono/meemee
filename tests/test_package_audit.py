import base64
import hashlib
import warnings
import zipfile

from meemee.package_audit import REQUIRED_WHEEL_PATHS, audit_wheel


def make_wheel(path, version="0.54.0", omit=None, license_expression="LicenseRef-Proprietary", license_text="Copyright. All rights reserved. This software is proprietary and confidential."):
    prefix = "meemee_agent-0.54.0.dist-info"
    files = {member: b"content" for member in REQUIRED_WHEEL_PATHS if member != omit}
    files[f"{prefix}/METADATA"] = f"Name: meemee-agent\nVersion: {version}\nLicense-Expression: {license_expression}\n".encode()
    files[f"{prefix}/licenses/LICENSE"] = license_text.encode()
    files[f"{prefix}/entry_points.txt"] = b"[console_scripts]\nmeemee = meemee.cli:app\n"
    rows = []
    for name, content in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=").decode()
        rows.append(f"{name},sha256={digest},{len(content)}")
    record = f"{prefix}/RECORD"
    files[record] = ("\n".join(rows) + f"\n{record},,\n").encode()
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in files.items(): archive.writestr(name, content)


def test_package_audit_accepts_complete_wheel(tmp_path):
    wheel = tmp_path / "meemee_agent-0.54.0-py3-none-any.whl"; make_wheel(wheel)
    assert audit_wheel(wheel, "0.54.0")["status"] == "pass"


def test_package_audit_finds_missing_asset_and_version(tmp_path):
    wheel = tmp_path / "broken.whl"; make_wheel(wheel, "old", "console/index.html")
    report = audit_wheel(wheel, "0.54.0")
    assert {finding["code"] for finding in report["findings"]} == {"wheel_content_missing", "wheel_version_mismatch"}


def test_package_audit_rejects_missing_or_invalid_commercial_license(tmp_path):
    missing = tmp_path / "missing.whl"; make_wheel(missing, license_expression="")
    assert "wheel_license_expression_invalid" in {item["code"] for item in audit_wheel(missing, "0.54.0")["findings"]}
    invalid = tmp_path / "invalid.whl"; make_wheel(invalid, license_text="empty")
    assert "wheel_license_invalid" in {item["code"] for item in audit_wheel(invalid, "0.54.0")["findings"]}


def test_package_audit_rejects_malformed_and_unsafe_archives(tmp_path):
    malformed = tmp_path / "malformed.whl"; malformed.write_bytes(b"not a zip")
    assert {item["code"] for item in audit_wheel(malformed, "0.54.0")["findings"]} == {"wheel_archive_invalid"}
    unsafe = tmp_path / "unsafe.whl"; make_wheel(unsafe)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(unsafe, "a") as archive:
            archive.writestr("../escape", "bad"); archive.writestr("console/index.html", "duplicate")
    codes = {item["code"] for item in audit_wheel(unsafe, "0.54.0")["findings"]}
    assert {"wheel_unsafe_path", "wheel_duplicate_member", "wheel_record_invalid"} <= codes


def test_package_audit_verifies_record_hashes_and_sizes(tmp_path):
    wheel = tmp_path / "tampered.whl"; make_wheel(wheel)
    rewritten = tmp_path / "rewritten.whl"
    with zipfile.ZipFile(wheel) as source, zipfile.ZipFile(rewritten, "w") as target:
        for member in source.infolist():
            content = source.read(member.filename)
            target.writestr(member, b"tampered" if member.filename == "console/index.html" else content)
    assert "wheel_record_invalid" in {item["code"] for item in audit_wheel(rewritten, "0.54.0")["findings"]}
