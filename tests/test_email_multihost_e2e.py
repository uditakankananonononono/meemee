"""Verification and password-reset links across hosts (PostgreSQL mode).

Two API servers with separate data directories share one PostgreSQL database behind an imagined
load balancer. Signup on A sends the verification email; the link is opened on B. A reset
requested on B is completed on A, and the new password logs in on B. Before this change the
challenge lived in each host's email-verifications.sqlite3, so the second host rejected the link.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from multihost_helpers import mail_capture, pg_hosts
from test_live_runs_e2e import PG_DSN, _headers

pytestmark = pytest.mark.skipif(not PG_DSN, reason="requires real PostgreSQL")


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    pytest.importorskip("uvicorn")
    with mail_capture() as (mail_url, sent):
        extra = {"MEEMEE_RESEND_API_KEY": "re_test", "MEEMEE_RESEND_API_URL": mail_url,
                 "MEEMEE_PUBLIC_URL": "https://meemee.test", "MEEMEE_SIGNUP_ENABLED": "true"}
        with pg_hosts(tmp_path_factory, extra_env=extra) as hosts:
            yield {"a": hosts["bases"][0], "b": hosts["bases"][1], "sent": sent}


def _link(sent: list[dict], kind: str, to: str) -> tuple[str, str]:
    mail = [m for m in sent if m["body"]["to"] == [to]][-1]
    match = re.search(rf"\?{kind}=([^&\"]+)&amp;account=([^\"&]+)|\?{kind}=([^&\"]+)&account=([^\"&]+)", mail["body"]["html"])
    assert match, mail["body"]["html"]
    groups = [g for g in match.groups() if g]
    return groups[0], groups[1]


def test_verification_link_from_host_a_is_accepted_on_host_b(env):
    a, b, sent = env["a"], env["b"], env["sent"]
    signup = httpx.post(f"{a}/v1/accounts/signup", timeout=30,
                        json={"email": "cross@example.com", "password": "a long enough password", "display_name": "Cross"})
    assert signup.status_code == 201 and signup.json()["verification_email"] == "sent", signup.text
    session = signup.json()["token"]
    assert sent[-1]["auth"] == "Bearer re_test" and sent[-1]["path"] == "/emails"
    token, account = _link(sent, "verify", "cross@example.com")
    assert httpx.get(f"{b}/v1/account", headers=_headers(session), timeout=10).json()["email_verified"] is False
    ok = httpx.post(f"{b}/v1/accounts/verify-email", json={"account_id": account, "token": token}, timeout=10)
    assert ok.status_code == 200, ok.text
    assert httpx.get(f"{a}/v1/account", headers=_headers(session), timeout=10).json()["email_verified"] is True
    replay = httpx.post(f"{a}/v1/accounts/verify-email", json={"account_id": account, "token": token}, timeout=10)
    assert replay.status_code == 400


def test_reset_requested_on_b_completes_on_a_and_logs_in_on_b(env):
    a, b, sent = env["a"], env["b"], env["sent"]
    signup = httpx.post(f"{a}/v1/accounts/signup", timeout=30,
                        json={"email": "reset@example.com", "password": "the old password", "display_name": "R"})
    old_session = signup.json()["token"]
    assert httpx.post(f"{b}/v1/accounts/forgot-password", json={"email": "reset@example.com"}, timeout=30).status_code == 202
    token, account = _link(sent, "reset", "reset@example.com")
    done = httpx.post(f"{a}/v1/accounts/reset-password", timeout=30,
                      json={"account_id": account, "token": token, "password": "the new password!"})
    assert done.status_code == 200, done.text
    assert httpx.get(f"{b}/v1/account", headers=_headers(old_session), timeout=10).status_code == 401
    assert httpx.post(f"{b}/v1/accounts/login", json={"email": "reset@example.com", "password": "the old password"}, timeout=30).status_code == 401
    assert httpx.post(f"{b}/v1/accounts/login", json={"email": "reset@example.com", "password": "the new password!"}, timeout=30).status_code == 200
    again = httpx.post(f"{b}/v1/accounts/reset-password", timeout=30,
                       json={"account_id": account, "token": token, "password": "another new password"})
    assert again.status_code == 400


def test_one_reset_link_clicked_on_both_hosts_at_once_succeeds_once(env):
    a, b, sent = env["a"], env["b"], env["sent"]
    httpx.post(f"{a}/v1/accounts/signup", timeout=30,
               json={"email": "race@example.com", "password": "the old password", "display_name": "Race"})
    httpx.post(f"{a}/v1/accounts/forgot-password", json={"email": "race@example.com"}, timeout=30)
    token, account = _link(sent, "reset", "race@example.com")

    def click(base):
        return httpx.post(f"{base}/v1/accounts/reset-password", timeout=30,
                          json={"account_id": account, "token": token, "password": f"new password via {base[-5:]}"}).status_code

    with ThreadPoolExecutor(6) as pool:
        codes = list(pool.map(click, [a, b] * 3))
    assert codes.count(200) == 1 and codes.count(400) == 5, codes
