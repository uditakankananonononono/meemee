import pytest

from meemee.connectors import ICSConnector, _ics_time


def test_kolkata_event_and_end_are_real_utc_instants(monkeypatch):
    body = b'BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:ist\r\nDTSTART;TZID=Asia/Kolkata:20261001T150000\r\nDTEND;TZID=Asia/Kolkata:20261001T160000\r\nSUMMARY:Team\r\n\t call\r\nEND:VEVENT\r\nEND:VCALENDAR'
    c = ICSConnector('ics', 'https://example.org/feed')
    monkeypatch.setattr(c, 'read', lambda: body)
    r = c.fetch('owner', 'calendar')[0]
    assert r.occurred_at == '2026-10-01T09:30:00+00:00'
    assert r.metadata['end_utc'] == '2026-10-01T10:30:00+00:00'
    assert r.title == 'Team call'
    assert r.metadata['timezone'] == 'Asia/Kolkata'


def test_utc_and_all_day():
    assert _ics_time('20261001T150000Z') == '2026-10-01T15:00:00+00:00'
    assert _ics_time('20261001') == '2026-10-01T00:00:00+00:00'


def test_floating_requires_configured_timezone():
    with pytest.raises(ValueError, match='default_timezone'):
        _ics_time('20261001T150000')
    assert _ics_time('20261001T150000', default_timezone='Asia/Kolkata') == '2026-10-01T09:30:00+00:00'


@pytest.mark.parametrize('value,tzid', [('bad', None), ('20261001T150000','Unknown/Zone'),
    ('20260308T023000','America/New_York'), ('20261101T013000','America/New_York'),
    ('20261001T150000Z','Asia/Kolkata')])
def test_invalid_nonexistent_and_ambiguous_fail_closed(value, tzid):
    with pytest.raises(ValueError):
        _ics_time(value, tzid)


def test_dst_offsets():
    assert _ics_time('20260701T120000','America/New_York').endswith('16:00:00+00:00')
    assert _ics_time('20260101T120000','America/New_York').endswith('17:00:00+00:00')
