"""NOT RUN. Executable specification model, NOT a production/storage adapter.

No SQLite/PG transaction, external provider or process crash proof. The inline
model makes the proposed interface explicit; no existing API binding implied.
"""
from copy import deepcopy
from math import isfinite

import pytest


def row(**changes):
    result = dict(id="c", owner="u", token="t1", generation=1, status="running",
                  claimed=10, expires=20, delivery="not_started", attempts=1, maximum=3,
                  permission=True)
    result.update(changes)
    return result


def valid_clock(value):
    if type(value) not in (int, float):
        return False
    try:
        return value >= 0 and isfinite(value)
    except OverflowError:
        return False


def advice(current, expected, now):
    for key in ("id", "owner", "token"):
        if (type(current[key]) is not str or not current[key]
                or len(current[key]) > 256 or current[key].strip() != current[key]
                or any(ord(c) < 32 or ord(c) == 127 for c in current[key])
                or current[key] != expected[key]):
            return "DENY"
    if (type(current["generation"]) is not int or current["generation"] < 1
            or type(expected["generation"]) is not int
            or current["generation"] != expected["generation"]):
        return "DENY"
    if current["status"] != "running":
        return "DENY"
    if not all(valid_clock(v) for v in (now, current["claimed"], current["expires"])):
        return "DENY"
    if current["expires"] <= current["claimed"] or now < current["claimed"]:
        return "DENY"
    if now < current["expires"]:
        return "DENY"  # every delivery state, before quarantine selection
    if (type(current["attempts"]) is not int or type(current["maximum"]) is not int
            or not 1 <= current["attempts"] <= current["maximum"]):
        return "DENY"
    if current["delivery"] != "not_started":
        return "QUARANTINE"
    return "REQUEST_RETRY" if current["attempts"] < current["maximum"] else "DENY"


class ContractModel:
    """Sequential atomic transitions only, with explicit simulated send counter."""

    def __init__(self):
        self.current = row()
        self.sends = 0
        self.messages = []

    def recover(self, expected, now):
        outcome = advice(self.current, expected, now)
        if outcome == "REQUEST_RETRY":
            self.current.update(status="queued", token="", delivery="not_started")
            return 1
        if outcome == "QUARANTINE":
            self.current.update(status="quarantined")
            return 1
        return 0

    def acquire(self):
        assert self.current["status"] == "queued"
        assert self.current["attempts"] < self.current["maximum"]
        self.current.update(status="running", generation=self.current["generation"] + 1,
                            token="t2", claimed=21, expires=31,
                            attempts=self.current["attempts"] + 1)

    def boundary(self, expected, now):
        if (any(self.current[k] != expected[k] for k in ("id", "owner", "token", "generation"))
                or self.current["status"] != "running" or not valid_clock(now)
                or not self.current["claimed"] <= now < self.current["expires"]
                or self.current["delivery"] != "not_started" or not self.current["permission"]):
            return 0
        self.current["delivery"] = "started"  # simulated durable commit BEFORE send
        return 1

    def send(self, expected, now):
        if not self.boundary(expected, now):
            return 0
        self.sends += 1
        return 1

    def settle(self, expected, operation, now):
        if (any(self.current[k] != expected[k] for k in ("id", "owner", "token", "generation"))
                or self.current["status"] != "running" or not valid_clock(now)
                or not self.current["claimed"] <= now < self.current["expires"]):
            return 0
        if operation == "fail":
            if self.current["delivery"] != "not_started":
                return 0
            self.current["status"] = "queued" if self.current["attempts"] < self.current["maximum"] else "failed"
        elif operation == "finish":
            if self.current["delivery"] != "accepted":
                return 0
            self.current["status"] = "done"
        else:
            raise ValueError(operation)
        return 1


@pytest.mark.parametrize("delivery", ["not_started", "started", "accepted", "unknown", "invalid"])
def test_active_all_delivery_states_deny_without_mutation(delivery):
    m = ContractModel()
    m.current["delivery"] = delivery
    before = deepcopy(m.current)
    assert advice(m.current, before, 19) == "DENY"
    assert m.recover(before, 19) == 0
    assert m.current == before and m.sends == 0


@pytest.mark.parametrize("delivery,outcome", [("not_started", "REQUEST_RETRY"),
                                             ("started", "QUARANTINE"),
                                             ("accepted", "QUARANTINE"),
                                             ("unknown", "QUARANTINE")])
def test_expiry_equality_and_ambiguous_never_retry(delivery, outcome):
    current = row(delivery=delivery)
    assert advice(current, deepcopy(current), 20) == outcome


@pytest.mark.parametrize("changes,now", [
    ({"expires": 10}, 20), ({"claimed": 21}, 20), ({}, float("nan")),
    ({}, float("inf")), ({}, True), ({"token": ""}, 20),
    ({"owner": ""}, 20), ({"generation": True}, 20),
    ({"attempts": True}, 20), ({"maximum": 2.5}, 20),
])
def test_invalid_input_denies_zero_changes(changes, now):
    m = ContractModel()
    m.current.update(changes)
    before = deepcopy(m.current)
    assert advice(m.current, before, now) == "DENY"
    assert m.recover(before, now) == 0
    assert m.current == before


def test_two_recoverers_exactly_one_transition_no_send():
    m = ContractModel()
    expected = deepcopy(m.current)
    assert [m.recover(expected, 20), m.recover(expected, 20)] == [1, 0]
    assert m.sends == 0


@pytest.mark.parametrize("changes", [{"expires": 30}, {"delivery": "started"},
                                     {"owner": "v"}, {"token": "t2"},
                                     {"generation": 2}, {"attempts": 3}])
def test_snapshot_stale_before_cas_rechecked(changes):
    m = ContractModel()
    expected = deepcopy(m.current)
    m.current.update(changes)
    before = deepcopy(m.current)
    changed = m.recover(expected, 20)
    if changes == {"delivery": "started"}:
        assert changed == 1 and m.current["status"] == "quarantined"
        assert m.recover(expected, 20) == 0
    else:
        assert changed == 0 and m.current == before
    assert m.sends == 0 and m.current["status"] != "queued"


@pytest.mark.parametrize("operation", ["finish", "fail"])
def test_old_worker_cannot_settle_new_generation(operation):
    m = ContractModel()
    old = deepcopy(m.current)
    assert m.recover(old, 20) == 1
    m.acquire()
    if operation == "finish":
        m.current["delivery"] = "accepted"
    before = deepcopy(m.current)
    assert m.settle(old, operation, 22) == 0
    assert m.current == before and m.sends == 0


@pytest.mark.parametrize("field,new", [("token", "t2"), ("generation", 2), ("owner", "v")])
def test_independent_fence_predicates_each_required(field, new):
    m = ContractModel()
    old = deepcopy(m.current)
    m.current.update({field: new, "delivery": "accepted"})
    before = deepcopy(m.current)
    assert m.settle(old, "finish", 15) == 0
    assert m.current == before


def test_send_loser_cannot_send_and_permission_revocation_blocks_boundary():
    m = ContractModel()
    expected = deepcopy(m.current)
    assert m.send(expected, 15) == 1
    assert m.send(expected, 15) == 0
    assert m.sends == 1 and m.current["delivery"] == "started"
    other = ContractModel()
    expected = deepcopy(other.current)
    other.current["permission"] = False
    assert other.send(expected, 15) == 0 and other.sends == 0


@pytest.mark.parametrize("kill", ["before_intent", "after_intent", "accepted_before_receipt", "receipt_before_finish"])
def test_kill_point_model_never_auto_retries_unknown_acceptance(kill):
    m = ContractModel()
    expected = deepcopy(m.current)
    if kill != "before_intent":
        assert m.boundary(expected, 15) == 1
    if kill in ("accepted_before_receipt", "receipt_before_finish"):
        m.sends += 1  # simulation, not provider delivery
    if kill == "receipt_before_finish":
        m.current["delivery"] = "accepted"
    outcome = advice(m.current, expected, 20)
    assert outcome == ("REQUEST_RETRY" if kill == "before_intent" else "QUARANTINE")
    original_sends = m.sends
    assert m.recover(expected, 20) == 1
    assert m.recover(expected, 20) == 0
    assert m.sends == original_sends
