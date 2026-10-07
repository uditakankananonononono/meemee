"""Terminal history must not claim transitions the job did not make."""
import pytest

from meemee.jobs import JobStore


@pytest.mark.parametrize('terminal',['finish','fail'])
def test_unclaimed_job_cannot_publish_terminal_event(tmp_path, terminal):
    store = JobStore(tmp_path/'jobs.db')
    ident = store.enqueue('work',principal='owner')
    before = store.events(ident)
    with pytest.raises(ValueError):
        if terminal == 'finish':
            store.finish(ident,{'final':'not executed'})
        else:
            store.fail(ident,'not executed')
    assert store.get(ident)['status'] == 'queued'
    assert store.events(ident) == before


def test_legacy_cancel_uses_cancelled_state_not_failed(tmp_path):
    store = JobStore(tmp_path/'jobs.db')
    ident = store.enqueue('work',principal='owner')
    assert store.cancel(ident)
    assert store.get(ident)['status'] == 'cancelled'
    assert store.events(ident)[-1]['kind'] == 'cancelled'
