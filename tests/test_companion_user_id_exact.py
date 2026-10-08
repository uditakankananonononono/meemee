"""Companion owner identifiers may not contain trailing control characters."""

import pytest
from pydantic import ValidationError

from meemee.companion.models import UserProfile


@pytest.mark.parametrize('user_id', ['owner\n', 'a' * 80 + '\n'])
def test_user_id_rejects_newline_and_overlong_control_suffix(user_id):
    with pytest.raises(ValidationError, match='user_id'):
        UserProfile(user_id=user_id, display_name='Owner')
