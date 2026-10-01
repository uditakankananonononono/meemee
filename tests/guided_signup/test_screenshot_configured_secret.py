from dataclasses import replace
from pathlib import Path

from meemee.guided_signup import Profile


def test_screenshot_blanks_configured_otp_selector(env, tmp_path):
    site, vault, inbox, profile, store, engine, password, token = env
    code_selector = '#otp'
    run = engine.start('owner', 'fixture', 'otp@example.test', 'OTP Test', 'signup-password')
    rid = run['id']
    page = engine.sessions[rid][1]
    # The code field appears after signup; rename the fixture field and configure that selector.
    engine.approve('owner', rid, run['inspection_digest'])
    engine.submit('owner', rid)
    page.locator(profile.code_selector).evaluate('(e) => { e.id = "otp"; e.value = "654321"; }')
    engine.profiles['fixture'] = replace(profile, code_selector=code_selector)
    persisted = store.get('owner', rid)
    persisted['profile_digest'] = engine.profiles['fixture'].digest
    store.save(persisted)
    target = tmp_path / 'otp.png'
    engine.screenshot('owner', rid, target)
    # Ensure screenshot happened and the configured OTP input is blank in the live page.
    assert target.exists()
    assert page.locator('#otp').input_value() == ''
