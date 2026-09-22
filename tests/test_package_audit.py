import warnings
import zipfile

from meemee.package_audit import REQUIRED_WHEEL_PATHS, audit_wheel


def make_wheel(path, version="0.54.0", omit=None, license_expression="LicenseRef-Proprietary", license_text="Copyright. All rights reserved. This software is proprietary and confidential."):
    with zipfile.ZipFile(path, "w") as archive:
        for member in REQUIRED_WHEEL_PATHS:
            if member != omit:
                archive.writestr(member, "content")
        archive.writestr("meemee_agent-0.54.0.dist-info/METADATA", f"Name: meemee-agent\nVersion: {version}\nLicense-Expression: {license_expression}\n")
        archive.writestr("meemee_agent-0.54.0.dist-info/licenses/LICENSE", license_text)
        archive.writestr("meemee_agent-0.54.0.dist-info/entry_points.txt", "[console_scripts]\nmeemee = meemee.cli:app\n")
        archive.writestr("meemee_agent-0.54.0.dist-info/RECORD", "")


def test_package_audit_accepts_complete_wheel(tmp_path):
    wheel = tmp_path / "meemee_agent-0.54.0-py3-none-any.whl"; make_wheel(wheel)
    assert audit_wheel(wheel, "0.54.0")["status"] == "pass"


def test_package_audit_finds_missing_asset_and_version(tmp_path):
    wheel = tmp_path / "broken.whl"; make_wheel(wheel, "old", "console/index.html")
    report = audit_wheel(wheel, "0.54.0")
    assert {finding["code"] for finding in report["findings"]} == {"wheel_content_missing", "wheel_version_mismatch"}


def test_package_audit_rejects_missing_or_invalid_commercial_license(tmp_path):
    missing = tmp_path / "missing.whl"
    make_wheel(missing, license_expression="")
    report = audit_wheel(missing, "0.54.0")
    assert "wheel_license_expression_invalid" in {item["code"] for item in report["findings"]}

    invalid = tmp_path / "invalid.whl"
    make_wheel(invalid, license_text="empty")
    report = audit_wheel(invalid, "0.54.0")
    assert "wheel_license_invalid" in {item["code"] for item in report["findings"]}


def test_package_audit_rejects_malformed_and_unsafe_archives(tmp_path):
    malformed = tmp_path / "malformed.whl"; malformed.write_bytes(b"not a zip")
    assert {item["code"] for item in audit_wheel(malformed, "0.54.0")["findings"]} == {"wheel_archive_invalid"}

    unsafe = tmp_path / "unsafe.whl"; make_wheel(unsafe)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(unsafe, "a") as archive:
            archive.writestr("../escape", "bad")
            archive.writestr("console/index.html", "duplicate")
    codes = {item["code"] for item in audit_wheel(unsafe, "0.54.0")["findings"]}
    assert {"wheel_unsafe_path", "wheel_duplicate_member"} <= codes
