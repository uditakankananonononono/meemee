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


# datetime.strptime accepts variable-width components. ICS grammar does not.
MALFORMED_WIDTH_TIMES = [
    '2026101T150000', '202611T150000', '202601001T150000',
    '20261001T15000', '20261001T1500', '20261001T1500000',
    '20261001T50000', '20261001T10500', '20261001T15001',
]


@pytest.mark.parametrize('value', MALFORMED_WIDTH_TIMES)
@pytest.mark.parametrize('mode', ['utc', 'floating', 'tzid'])
def test_wrong_width_datetime_rejected_before_conversion(value, mode):
    kwargs = {}
    if mode == 'utc':
        value += 'Z'
    elif mode == 'floating':
        kwargs['default_timezone'] = 'Asia/Kolkata'
    else:
        kwargs['tzid'] = 'Asia/Kolkata'
    with pytest.raises(ValueError, match='invalid ICS timestamp grammar'):
        _ics_time(value, **kwargs)


@pytest.mark.parametrize('value', [
    '2026101', '202611', '202610001', '20261001Z',
    '２０２６１００１', '20261001t150000', '20261001T150000z',
    '2026-10-01T150000Z', '20261001T15:00:00Z',
    ' 20261001T150000Z', '20261001T150000Z\n', '',
])
def test_noncanonical_grammar_rejected(value):
    with pytest.raises(ValueError, match='invalid ICS timestamp grammar'):
        _ics_time(value, default_timezone='Asia/Kolkata')


@pytest.mark.parametrize('value', [
    '20260229', '20261301', '20261032',
    '20260229T150000Z', '20261001T240000Z',
    '20261001T156000Z', '20261001T150061Z',
])
def test_canonical_width_still_validates_calendar_and_clock(value):
    with pytest.raises(ValueError):
        _ics_time(value)


def test_valid_leap_date_and_explicit_zone_precedence():
    assert _ics_time('20240229') == '2024-02-29T00:00:00+00:00'
    assert _ics_time('20240229T000001Z') == '2024-02-29T00:00:01+00:00'
    assert _ics_time('20261001T150000', 'Asia/Kolkata',
                     default_timezone='America/New_York') == '2026-10-01T09:30:00+00:00'


@pytest.mark.parametrize('property_name', ['DTSTART', 'DTEND'])
@pytest.mark.parametrize('qualifier,suffix,default_timezone', [
    ('', 'Z', None), ('', '', 'Asia/Kolkata'), (';TZID=Asia/Kolkata', '', None),
])
def test_connector_rejects_wrong_width_start_or_end(
        monkeypatch, property_name, qualifier, suffix, default_timezone):
    start = '20261001T150000'
    end = '20261001T160000'
    if property_name == 'DTSTART':
        start = '2026101T150000'
    else:
        end = '20261001T16000'
    body = (f'BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:bad-width\r\n'
            f'DTSTART{qualifier}:{start}{suffix}\r\n'
            f'DTEND{qualifier}:{end}{suffix}\r\n'
            'END:VEVENT\r\nEND:VCALENDAR').encode()
    connector = ICSConnector('ics', 'https://example.org/feed',
                             default_timezone=default_timezone)
    monkeypatch.setattr(connector, 'read', lambda: body)
    with pytest.raises(ValueError, match='invalid ICS timestamp grammar'):
        connector.fetch('owner', 'calendar')
