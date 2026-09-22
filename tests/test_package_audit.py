import zipfile

from meemee.package_audit import REQUIRED_WHEEL_PATHS, audit_wheel


def make_wheel(path, version="0.52.0", omit=None):
    with zipfile.ZipFile(path, "w") as archive:
        for member in REQUIRED_WHEEL_PATHS:
            if member != omit:
                archive.writestr(member, "content")
        archive.writestr("meemee_agent-0.52.0.dist-info/METADATA", f"Name: meemee-agent\nVersion: {version}\n")
        archive.writestr("meemee_agent-0.52.0.dist-info/entry_points.txt", "[console_scripts]\nmeemee = meemee.cli:app\n")


def test_package_audit_accepts_complete_wheel(tmp_path):
    wheel = tmp_path / "meemee_agent-0.52.0-py3-none-any.whl"; make_wheel(wheel)
    assert audit_wheel(wheel, "0.52.0")["status"] == "pass"


def test_package_audit_finds_missing_asset_and_version(tmp_path):
    wheel = tmp_path / "broken.whl"; make_wheel(wheel, "old", "console/index.html")
    report = audit_wheel(wheel, "0.52.0")
    assert {finding["code"] for finding in report["findings"]} == {"wheel_content_missing", "wheel_version_mismatch"}
