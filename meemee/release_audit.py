from __future__ import annotations

import re
from pathlib import Path

REQUIRED = (
    "README.md", "STATUS.md", "CHANGELOG.md", "OPERATIONS.md", "LICENSE",
    "pyproject.toml", "Dockerfile", ".env.example",
)
TEXT_SUFFIXES = {".py", ".js", ".html", ".css", ".md", ".toml", ".yaml", ".yml"}
STUB_PATTERNS = (
    re.compile(r"\bTODO\b"), re.compile(r"\bFIXME\b"),
    re.compile("Not" + r"Implemented(?:Error)?"),
    re.compile(r"raise\s+AssertionError\([\"']stub", re.IGNORECASE),
)
SKIP_PARTS = {".git", ".venv", "node_modules", "__pycache__"}


def _version(path: Path) -> str | None:
    match = re.search(r'^(?:__version__|version)\s*=\s*["\']([^"\']+)', path.read_text(), re.MULTILINE)
    return match.group(1) if match else None


def audit_tree(root: Path, expected_version: str | None = None) -> dict:
    """Apply deterministic release invariants to the exact source tree."""
    findings: list[dict] = []
    for relative in REQUIRED:
        if not (root / relative).is_file():
            findings.append({"code": "missing_required_file", "path": relative})
    pyproject = root / "pyproject.toml"
    package = root / "meemee" / "__init__.py"
    if expected_version is not None:
        actual = _version(pyproject) if pyproject.exists() else None
        if actual != expected_version:
            findings.append({
                "code": "expected_version_mismatch",
                "path": "pyproject.toml",
                "expected": expected_version,
                "actual": actual,
            })
    if pyproject.exists() and package.exists():
        versions = {_version(pyproject), _version(package)}
        if None in versions or len(versions) != 1:
            findings.append({"code": "version_source_drift", "path": "pyproject.toml"})
        else:
            version = next(iter(versions))
            for document in ("README.md", "STATUS.md", "CHANGELOG.md"):
                target = root / document
                if target.exists() and version not in target.read_text():
                    findings.append({"code": "version_document_drift", "path": document})
    sdk_project = root / "sdk" / "pyproject.toml"
    sdk_package = root / "sdk" / "src" / "meemee_client" / "_version.py"
    if pyproject.exists() and sdk_project.exists() and sdk_package.exists():
        versions = {_version(pyproject), _version(sdk_project), _version(sdk_package)}
        if None in versions or len(versions) != 1:
            findings.append({"code":"sdk_version_drift","path":"sdk/pyproject.toml"})
    readme = root / "README.md"
    if readme.exists() and pyproject.exists():
        text = readme.read_text()
        heading = re.search(r"^## Verified in v([^ ]+) \((\d+)\)$", text, re.MULTILINE)
        numbered = [int(value) for value in re.findall(r"^(\d+)\. ", text, re.MULTILINE)]
        if not heading or heading.group(1) != _version(pyproject) or not numbered or int(heading.group(2)) != max(numbered):
            findings.append({"code":"verified_ledger_drift","path":"README.md"})
    stale_claims = {
        "README.md": (
            "WebSocket streaming (resume-safe SSE is implemented)",
            "runtime still uses SQLite",
            "remote Git push and pull-request operations",
            "Semantic/embedding memory and reranking",
        ),
        "STATUS.md": ("still instantiate SQLite stores",),
    }
    for relative, phrases in stale_claims.items():
        target = root / relative
        if target.exists():
            text = target.read_text()
            for phrase in phrases:
                if phrase in text:
                    findings.append({"code":"stale_capability_claim","path":relative,"phrase":phrase})
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in TEXT_SUFFIXES:
            continue
        if any(part in SKIP_PARTS for part in path.parts):
            continue
        relative = str(path.relative_to(root))
        for number, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
            for pattern in STUB_PATTERNS:
                if pattern.search(line):
                    findings.append({
                        "code": "stub_marker", "path": relative, "line": number,
                        "pattern": pattern.pattern,
                    })
    return {
        "status": "pass" if not findings else "fail",
        "findings": findings,
        "summary": {"findings": len(findings)},
    }
