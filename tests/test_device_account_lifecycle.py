"""Device registry and NativeClient journal must be exported and hard-deleted per owner."""
import json
import sqlite3

import pytest

from meemee.account_deletion import build_account_purger
from meemee.account_export import export_account, inspect_import
from meemee.config import Settings
from meemee.devices import DeviceRegistry
from meemee.native_device import MAX_WINDOW_ID, NativeClient, WindowsAdapter, X11Adapter, _window_id
from meemee.persistence import persistence_from_settings


class FakeAdapter:
    def __init__(self, marker):
        self.marker = marker

    def observe(self):
        return {'title': f'win-{self.marker}', 'geometry': '1x1', 'platform': 'fake', 'observed_at': 'now'}

    def set_title(self, title):
        return {'title': title, 'platform': 'fake', 'observed_at': 'now'}


def enroll(data, owner, marker):
    registry = DeviceRegistry(data / 'devices.sqlite3')
    pairing = registry.create_pairing(owner)
    creds = registry.pair(pairing['pairing_id'], pairing['code'], device_id=f'dev-{owner}',
                          name=f'laptop-{marker}', capabilities=NativeClient.manifest())
    client = NativeClient(registry, owner, creds['device_id'], creds['secret'], FakeAdapter(marker),
                          data / 'native-device.sqlite3')
    client.execute(registry.issue(owner, creds['device_id'], 'window.observe', {}))
    client.execute(registry.issue(owner, creds['device_id'], 'window.set_title', {'title': f'title-{marker}'}))
    return registry, creds


def raw(data):
    return {p.name: p.read_bytes() for p in data.glob('*.sqlite3*')}


def test_export_and_purge_cover_device_stores_two_owners(tmp_path, monkeypatch):
    data = tmp_path / 'data'
    data.mkdir()
    reg_a, creds_a = enroll(data, 'alice', 'zebramark')
    reg_b, _creds_b = enroll(data, 'bob', 'yakmark')
    # Markers really are on disk before the purge (otherwise the raw check proves nothing).
    blobs = raw(data)
    assert any(b'zebramark' in b for n, b in blobs.items() if n.startswith('devices.sqlite3'))
    assert any(b'zebramark' in b for n, b in blobs.items() if n.startswith('native-device.sqlite3'))
    assert creds_a['secret'].encode() in b''.join(b for n, b in blobs.items() if n.startswith('devices.sqlite3'))

    out = tmp_path / 'export.json'
    report = export_account(data, 'alice', out)
    text = out.read_text()
    plan = inspect_import(out)  # re-verifies the checksum over the whole payload, devices included
    assert plan['sha256'] == report['sha256']
    devices = json.loads(text)['payload']['local_devices']
    assert [d['device_id'] for d in devices['devices']] == ['dev-alice']
    assert len(devices['device_commands']) == 2 and len(devices['native_commands']) == 2
    assert report['local_devices'] == {k: len(v) for k, v in devices.items()}
    assert 'zebramark' in text and 'yakmark' not in text and 'dev-bob' not in text
    tampered = json.loads(text)
    tampered['payload']['local_devices']['devices'][0]['name'] = 'forged'
    forged = tmp_path / 'forged.json'
    forged.write_text(json.dumps(tampered))
    with pytest.raises(ValueError, match='checksum'):
        inspect_import(forged)

    monkeypatch.setenv('MEEMEE_DATA_DIR', str(data))
    settings = Settings()
    result = build_account_purger(settings, persistence_from_settings(settings)).purge('alice', requested_by='test')
    assert result['status'] == 'completed'
    assert result['steps']['local_devices'] == {
        'device_pairings': 1, 'devices': 1, 'device_commands': 2, 'native_commands': 2}

    for name, content in raw(data).items():
        if name.startswith('account-deletions'):
            continue  # deliberately keeps the principal id only
        for needle in (b'zebramark', creds_a['secret'].encode(), b'dev-alice'):
            assert needle not in content, (name, needle)
    db = sqlite3.connect(data / 'devices.sqlite3')
    assert db.execute("SELECT count(*) FROM devices WHERE owner_id='alice'").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM devices WHERE owner_id='bob'").fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM device_commands WHERE device_id='dev-bob'").fetchone()[0] == 2
    db.close()
    db = sqlite3.connect(data / 'native-device.sqlite3')
    assert db.execute("SELECT count(*) FROM native_commands WHERE device_id='dev-alice'").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM native_commands WHERE device_id='dev-bob'").fetchone()[0] == 2
    db.close()
    assert b'yakmark' in b''.join(b for n, b in raw(data).items() if n.startswith('devices.sqlite3'))
    assert b'yakmark' in b''.join(b for n, b in raw(data).items() if n.startswith('native-device.sqlite3'))
    # Idempotent: a second purge (e.g. resume) finds nothing and does not touch bob.
    again = build_account_purger(settings, persistence_from_settings(settings)).purge('alice', requested_by='test')
    assert again['steps']['local_devices'] == {
        'device_pairings': 0, 'devices': 0, 'device_commands': 0, 'native_commands': 0}
    reg_a.db.close(); reg_b.db.close()


def test_purge_without_device_files_is_a_noop(tmp_path, monkeypatch):
    monkeypatch.setenv('MEEMEE_DATA_DIR', str(tmp_path / 'data'))
    settings = Settings()
    result = build_account_purger(settings, persistence_from_settings(settings)).purge('carol', requested_by='test')
    assert result['steps']['local_devices']['devices'] == 0
    assert not (tmp_path / 'data' / 'devices.sqlite3').exists()


def test_new_stores_enable_secure_delete(tmp_path):
    registry = DeviceRegistry(tmp_path / 'd.sqlite3')
    client = NativeClient(registry, 'o', 'x', '00', FakeAdapter('m'), tmp_path / 'j.sqlite3')
    assert registry.db.execute('PRAGMA secure_delete').fetchone()[0] == 1
    assert client.db.execute('PRAGMA secure_delete').fetchone()[0] == 1


@pytest.mark.parametrize('bad', [MAX_WINDOW_ID + 1, 2 ** 63, 2 ** 64, 10 ** 30])
def test_window_ids_have_an_upper_bound(bad, monkeypatch):
    monkeypatch.setenv('DISPLAY', ':99')
    with pytest.raises(ValueError, match='window id'):
        _window_id(bad)
    with pytest.raises(ValueError, match='window id'):
        X11Adapter(bad)
    monkeypatch.setenv('MEEMEE_WINDOWS_ADAPTER_UNVERIFIED_OK', '1')
    monkeypatch.setattr('os.name', 'nt')
    with pytest.raises(ValueError, match='window id'):
        WindowsAdapter(bad)


def test_window_id_upper_boundary_is_accepted():
    assert _window_id(MAX_WINDOW_ID) == MAX_WINDOW_ID and _window_id(1) == 1
