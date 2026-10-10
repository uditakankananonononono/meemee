import hashlib
from dataclasses import FrozenInstanceError, replace
import pytest
from meemee.monitor_event_identity import identify_event, EventIdentity


def event(payload=None, **kwargs):
    return identify_event('owner', 'source', 'event', {} if payload is None else payload, **kwargs)


def test_new_canonical_detached_and_replay():
    payload = {'z': [True, None, 'é'], 'a': 1}
    first = event(payload)
    assert first.status == 'new'
    assert first.canonical_payload == '{"a":1,"z":[true,null,"é"]}'
    assert first.identity.payload_sha256 == hashlib.sha256(first.canonical_payload.encode()).hexdigest()
    assert event({'a': 1, 'z': [True, None, 'é']}, prior=first.identity).status == 'replay'
    payload['z'].append('mutated')
    assert event(payload, prior=first.identity).status == 'conflict'
    assert 'mutated' not in first.canonical_payload
    with pytest.raises(FrozenInstanceError):
        first.identity.owner_id = 'changed'


def test_distinct_event_identical_payload_not_deduped():
    a = identify_event('o', 's', 'a', {'v': 1})
    b = identify_event('o', 's', 'b', {'v': 1})
    assert a.status == b.status == 'new' and a.identity != b.identity
    assert a.identity.payload_sha256 == b.identity.payload_sha256


@pytest.mark.parametrize('field', ['owner_id', 'source_id', 'event_id'])
def test_wrong_prior_key_refuses(field):
    with pytest.raises(ValueError, match='mismatch'):
        event(prior=replace(event().identity, **{field: 'other'}))


@pytest.mark.parametrize('value', ['', ' x', 'x ', 'x\x00', 'x\n', 'x\x7f', '\ud800', 'x'*241, 1, True])
def test_invalid_identity(value):
    with pytest.raises((TypeError, ValueError)):
        identify_event(value, 'source', 'event', {})


@pytest.mark.parametrize('value', [float('nan'), float('inf'), -float('inf'), 2**257, ('x',), {1:'x'}, object(), '\ud800'])
def test_invalid_payload(value):
    with pytest.raises((TypeError, ValueError)):
        event({'v': value})


@pytest.mark.parametrize('cap', [True, 0, -1, 32769, 1.0, '10'])
def test_invalid_caps(cap):
    with pytest.raises(ValueError):
        event(max_payload_bytes=cap)


def test_exact_utf8_byte_boundary_and_escaping():
    text = event({'x': 'é'}).canonical_payload
    size = len(text.encode())
    assert event({'x': 'é'}, max_payload_bytes=size).canonical_payload == text
    with pytest.raises(ValueError):
        event({'x': 'é'}, max_payload_bytes=size-1)
    with pytest.raises(ValueError):
        event({'x': '\n'*10}, max_payload_bytes=20)


def test_depth_width_cycles_and_huge_string():
    cycle = []; cycle.append(cycle)
    nested = 0
    for _ in range(18): nested = [nested]
    for v in [cycle, nested, list(range(4097)), {str(i): i for i in range(101)}, 'x'*32769]:
        with pytest.raises(ValueError): event({'v': v})


@pytest.mark.parametrize('digest', ['A'*64, 'x'*64, 'a'*63, 1])
def test_prior_digest_refused(digest):
    with pytest.raises(ValueError): event(prior=replace(event().identity, payload_sha256=digest))


def test_exact_shapes_and_type_distinctions():
    with pytest.raises(TypeError): identify_event('o','s','e',[])
    with pytest.raises(TypeError): event(prior={})
    a=event({'v': 1})
    assert event({'v': True},prior=a.identity).status=='conflict'
    assert event({'v': 1.0},prior=a.identity).status=='conflict'
    class D(dict): pass
    with pytest.raises(TypeError): event(D())
