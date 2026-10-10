"""NOT RUN. Source provenance checks, NOT runtime recovery acceptance.

Base/reference-byte hashes expected GREEN; absent/mismatched bytes or symbols RED.
"""
import ast
import hashlib
import json
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = json.loads((ROOT / "docs/prep/MM-R1-review-manifest.json").read_text())


@pytest.mark.parametrize("entry", MANIFEST["source_files"], ids=lambda e: e["path"])
def test_exact_base_file_and_symbol_hashes(entry):
    data = subprocess.check_output(
        ["git", "show", MANIFEST["base"] + ":" + entry["path"]], cwd=ROOT,
    )
    assert hashlib.sha256(data).hexdigest() == entry["sha256"]
    if not entry["symbols"]:
        return
    text = data.decode()
    lines = text.splitlines(keepends=True)
    nodes = {}
    for node in ast.parse(text).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            nodes[node.name] = node
        if isinstance(node, ast.ClassDef):
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    nodes[node.name + "." + child.name] = child
    for symbol in entry["symbols"]:
        assert symbol["name"] in nodes, "ABSENT symbol, never invent binding"
        node = nodes[symbol["name"]]
        start = min([node.lineno] + [d.lineno for d in node.decorator_list])
        assert (start, node.end_lineno) == (symbol["start_line"], symbol["end_line"])
        content = "".join(lines[start - 1:node.end_lineno]).encode()
        assert hashlib.sha256(content).hexdigest() == symbol["sha256"]


def test_reference_bytes_and_symbol_hashes_required():
    reference = MANIFEST["reference"]
    assert reference["status"] == "PATCH_BYTES_VERIFIED_COMMIT_OBJECT_UNAVAILABLE"
    patch = (ROOT / reference["patch_path"]).read_bytes()
    assert hashlib.sha256(patch).hexdigest() == reference["patch_sha256"]
    assert reference["patch_sha256"] == reference["peer_reported_patch_sha256"]
    assert patch.decode().splitlines()[0].split()[1] == reference["commit"]
    parts = {}
    current = None
    for line in patch.decode().splitlines(keepends=True):
        if line.startswith("diff --git "):
            current = line.split(" b/", 1)[1].strip()
            parts[current] = []
        elif current and line.startswith("+") and not line.startswith("+++"):
            parts[current].append(line[1:])
    assert set(parts) == {"meemee/checkin_recovery_policy.py", "tests/test_checkin_recovery_policy.py"}
    for entry in reference["files"]:
        assert hashlib.sha256("".join(parts[entry["path"]]).encode()).hexdigest() == entry["sha256"]
    for symbol in reference["symbol_hashes"]:
        lines = parts[symbol["path"]]
        nodes = {n.name: n for n in ast.parse("".join(lines)).body
                 if isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))}
        assert symbol["name"] in nodes
        node = nodes[symbol["name"]]
        start = min([node.lineno] + [d.lineno for d in node.decorator_list])
        assert (start, node.end_lineno) == (symbol["start_line"], symbol["end_line"])
        content = "".join(lines[start - 1:node.end_lineno]).encode()
        assert hashlib.sha256(content).hexdigest() == symbol["sha256"]
