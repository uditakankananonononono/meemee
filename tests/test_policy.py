from meemee.policy import PolicyEngine
from meemee.types import Risk


def test_policy_is_deny_first():
    policy = PolicyEngine({"deny_tools":["git.commit"], "auto_approve_risks":["read","write"]})
    result = policy.evaluate("git.commit", {}, Risk.WRITE)
    assert not result.allowed and not result.require_approval


def test_policy_allowlist_and_approval():
    policy = PolicyEngine({"allow_tools":["workspace.read_file","workspace.write_file"]})
    assert policy.evaluate("workspace.read_file", {"path":"README.md"}, Risk.READ).allowed
    write = policy.evaluate("workspace.write_file", {"path":"x"}, Risk.WRITE)
    assert write.allowed and write.require_approval
    assert not policy.evaluate("git.inspect", {}, Risk.READ).allowed


def test_policy_denies_path_host_and_large_arguments():
    policy = PolicyEngine({"deny_paths":["**/.env","secret*"], "deny_hosts":["*.corp.test"], "max_argument_bytes":50})
    assert not policy.evaluate("x", {"path":"a/.env"}, Risk.READ).allowed
    assert not policy.evaluate("x", {"url":"https://private.corp.test/x"}, Risk.READ).allowed
    assert not policy.evaluate("x", {"value":"x"*100}, Risk.READ).allowed
