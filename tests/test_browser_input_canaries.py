"""Reject unsafe browser arguments before any browser launch or navigation."""
import pytest
from pydantic import ValidationError

from meemee.tools.browser import BrowseArgs, BrowserAction


@pytest.mark.parametrize('profile', ['.', '..'])
def test_profile_cannot_be_parent_or_current_directory(profile):
    with pytest.raises(ValidationError):
        BrowseArgs(url='https://example.com', profile=profile)


@pytest.mark.parametrize('kind', ['click', 'fill', 'press', 'select'])
def test_selector_actions_fail_before_browser_side_effects(kind):
    with pytest.raises(ValidationError):
        BrowserAction(kind=kind)
