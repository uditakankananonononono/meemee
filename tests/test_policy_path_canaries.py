"""A denied path cannot be bypassed with equivalent lexical spellings."""
import pytest

from meemee.policy import PolicyEngine
from meemee.types import Risk


@pytest.mark.parametrize('path', ['./.env','sub/../.env','sub//../.env'])
def test_path_deny_policy_covers_equivalent_normalized_paths(path):
    policy = PolicyEngine({'deny_paths':['.env']})
    assert not policy.evaluate('workspace.read_file',{'path':path},Risk.READ).allowed


def test_download_destination_obeys_denied_path_policy():
    policy = PolicyEngine({'deny_paths':['private/*']})
    assert not policy.evaluate('browser.navigate',{'download_dir':'private/files'},Risk.WRITE).allowed
