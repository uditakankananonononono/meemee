"""MM-PREP-T1 authored NOT RUN. Oracle/source contracts, NOT runtime repair proof.

Source tests parse files, not import production modules. Oracle models only
normalized facts and effect ordering. A trusted flag is a test fact, never a
proposed client field. Synthetic hexadecimal digest values are not secrets.
No DB CAS, transport authentication, Chromium or process crash is exercised.
"""
import ast
import json
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CASES = json.loads(Path(__file__).with_name("mm_prep_t1_cases.json").read_text())
BASE_FACTS = dict(
    principal="owner", trusted=True, owner="owner", admin=False,
    requested_by="agent", notify_recipient="someone", session_id="s", expected_session="s",
    takeover_id="t", expected_takeover="t", active_id="t", state="awaiting_human",
    live=True, closed=False, finished=False, claimed=False, outcome=None,
    now=100, expires=101, digest="a" * 64, stored_digest="a" * 64,
)
EFFECTS = ("manager_human", "claim_event", "socket_claimed", "frame_start")


def predicates(facts):
    """Test-only oracle. Not a token validator, authenticator or persistence API."""
    principal = facts["principal"]
    digest = facts["digest"]
    return {
        "owner": facts["trusted"] is True and type(principal) is str
        and bool(principal) and principal == facts["owner"],
        "ids": facts["session_id"] == facts["expected_session"]
        and facts["takeover_id"] == facts["expected_takeover"],
        "digest": type(digest) is str and len(digest) == 64
        and all(c in "0123456789abcdef" for c in digest)
        and digest == facts["stored_digest"],
        "lifecycle": not facts["closed"] and not facts["finished"]
        and facts["outcome"] is None and facts["state"] == "awaiting_human"
        and facts["live"] is True and facts["now"] < facts["expires"],
        "claimed_at": facts["claimed"] is False,
        "current_takeover": facts["active_id"] == facts["takeover_id"],
    }


def allow(facts, omit=None):
    return all(value for name, value in predicates(facts).items() if name != omit)


def effects_after_result(facts, affected_rows, omit=None):
    """Injected integer result is an ordering oracle, NOT evidence of DB CAS."""
    if not allow(facts, omit):
        return ()
    if omit != "winner_gate" and (type(affected_rows) is not int or affected_rows != 1):
        return ()
    return EFFECTS


def source(path, symbol):
    text = (ROOT / path).read_text()
    nodes = ast.parse(text).body
    for part in symbol.split("."):
        node = next(n for n in nodes if getattr(n, "name", None) == part)
        nodes = getattr(node, "body", [])
    return textwrap.dedent(ast.get_source_segment(text, node))


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_negative_matrix_blocks_initial_effects(case):
    facts = {**BASE_FACTS, **case["changes"]}
    assert allow(facts) is case["allow"]
    # Even a fake "winner" cannot override an invalid initial claim.
    assert effects_after_result(facts, 1) == (EFFECTS if case["allow"] else ())


@pytest.mark.parametrize("rows", [0, 2, -1, None, True, "1"])
def test_exactly_one_row_required_before_effects(rows):
    assert effects_after_result(BASE_FACTS, rows) == ()


def test_unchanged_snapshot_allows_twice_without_reservation():
    facts = BASE_FACTS.copy()
    assert allow(facts) and allow(facts)
    assert facts == BASE_FACTS
    assert facts["claimed"] is False


@pytest.mark.parametrize("mutation,changes,rows", [
    ("owner", {"principal": "other"}, 1),
    ("claimed_at", {"claimed": True}, 1),
    ("current_takeover", {"active_id": "other"}, 1),
    ("winner_gate", {}, 0),
])
def test_contract_kills_removed_predicate_mutants(mutation, changes, rows):
    facts = {**BASE_FACTS, **changes}
    assert effects_after_result(facts, rows) == ()
    assert effects_after_result(facts, rows, omit=mutation) == EFFECTS


def test_pin_observation_socket_hello_token_only():
    text = source("meemee/browser_api.py", "build_browser_router.takeover_socket")
    assert 'hello.get("takeover_id"' in text and 'hello.get("token"' in text
    assert "manager.claim(takeover_id, token)" in text


@pytest.mark.parametrize("path", ["meemee/browser_sessions.py", "meemee_persist_pg/browser.py"])
def test_pin_observation_both_stores_coalesce_no_first_claim_result(path):
    text = source(path, "BrowserSessionStore.claim_takeover")
    assert "COALESCE" in text
    assert "-> None" in text
    assert "rowcount" not in text and "RETURNING" not in text


def test_pin_observation_manager_has_unconditional_effects():
    text = source("meemee/browser_sessions.py", "BrowserSessionManager.claim")
    assert "self.store.claim_takeover(takeover_id)" in text
    assert 'state="human"' in text
    assert "affected_rows" not in text


# Acceptance debt: these assertions are intentionally expected RED at the pin.
# They are not xfail/skip. Their pass would still require semantic audit of auth,
# CAS and effect gating; mere source-shape changes cannot prove those properties.
def test_expected_red_initial_claim_requires_trusted_principal():
    text = source("meemee/browser_sessions.py", "BrowserSessionManager.claim")
    signature = ast.parse(text).body[0].args
    assert len(signature.args) > 3, "base has only self, takeover_id, token"


@pytest.mark.parametrize("path", ["meemee/browser_sessions.py", "meemee_persist_pg/browser.py"])
def test_expected_red_store_has_first_claim_cas_result(path):
    text = source(path, "BrowserSessionStore.claim_takeover")
    assert "COALESCE" not in text
    assert "claimed_at IS NULL" in text
    assert "rowcount" in text or "RETURNING" in text


def test_expected_red_handshake_does_not_expose_exception_reason():
    text = source("meemee/browser_api.py", "build_browser_router.takeover_socket")
    handshake = text.split('audit_event("human"', 1)[0]
    assert "str(exc)" not in handshake
