import pytest

from meemee.guided_signup.engine import Engine, Profile
from test_signup import env, start  # noqa: F401


def engine_with(env, hidden):
    site, vault, inbox, p, store, e, password, token = env
    profile = Profile('fixture', site.origin, hidden_fields=hidden)
    e.profiles['fixture'] = profile        # one Playwright driver per thread: swap the reviewed profile in place
    return e


def test_reviewed_csrf_token_is_carried_and_account_created(env):
    site = env[0]
    site.mode = 'csrf'
    e = engine_with(env, ('authenticity_token',))
    run = e.start('owner', 'fixture', 'owner@example.test', 'Fixture Owner', 'signup-password')
    assert run['state'] == 'ready', run
    e.approve('owner', run['id'], run['inspection_digest'])
    run = e.submit('owner', run['id'])
    assert run['state'] == 'verification', run
    assert site.submits == 1 and 'owner@example.test' in site.accounts


def test_hidden_input_without_profile_review_stops_before_any_post(env):
    site = env[0]
    site.mode = 'csrf'
    run = start(env)
    assert run['state'] == 'stopped' and run['reason'] == 'unknown_form_controls'
    assert site.submits == 0


def test_unreviewed_extra_hidden_input_stops_even_with_token_reviewed(env):
    site = env[0]
    site.mode = 'csrf-extra'
    e = engine_with(env, ('authenticity_token',))
    run = e.start('owner', 'fixture', 'owner@example.test', 'Fixture Owner', 'signup-password')
    assert run['state'] == 'stopped' and run['reason'] == 'unknown_form_controls'
    assert site.submits == 0


def test_hidden_field_names_validated():
    with pytest.raises(ValueError):
        Profile('x', 'http://127.0.0.1:9', hidden_fields=('a b',))
    with pytest.raises(ValueError):
        Profile('x', 'http://127.0.0.1:9', hidden_fields=('t', 't'))


@pytest.mark.parametrize('mode', ['outside-hidden', 'form-attr'])
def test_hidden_or_form_bound_controls_outside_the_form_stop(env, mode):
    site = env[0]
    site.mode = mode
    run = start(env)
    assert run['state'] == 'stopped' and run['reason'] == 'unknown_form_controls'
    assert site.submits == 0
