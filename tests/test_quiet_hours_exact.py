"""Quiet-hour input must be exactly HH:MM, without trailing control characters."""

import pytest
from pydantic import ValidationError

from meemee.companion.models import QuietHours


@pytest.mark.parametrize('field', ['start', 'end'])
def test_quiet_hours_rejects_trailing_newline(field):
    values = {'start': '22:00', 'end': '07:00'}
    values[field] += '\n'
    with pytest.raises(ValidationError, match='HH:MM'):
        QuietHours(**values)
