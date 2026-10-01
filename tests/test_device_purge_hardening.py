"""Auditor scenarios for the device purge: stale freelist pages, journal race, crash/resume, orphans."""
import sqlite3
import threading

import pytest

from meemee.account_deletion import build_account_purger
from meemee.config import Settings
from meemee.devices import LocalDeviceStores
from meemee.native_device import NativeClient
from meemee.persistence import persistence_from_settings
from tests.test_device_account_lifecycle import FakeAdapter, enroll, raw


def purger(data, monkeypatch):
    monkeypatch.setenv('MEEMEE_DATA_DIR', str(data))
    settings = Settings()
    return build_account_purger(settings, persistence_from_settings(settings))


def mk(tmp_path):
    data = tmp_path / 'data'
    data.mkdir()
    return data


def blob(data, prefix):
    return b''.join(b for n, b in raw(data).items() if n.startswith(prefix))


def test_stale_freelist_pages_are_removed(tmp_path, monkeypatch):
    data = mk(tmp_path)
    reg, creds = enroll(data, 'alice', 'quailmark')
    enroll(data, 'bob', 'yakmark')
    reg.db.close()
    # An older build deleted alice's command rows with secure_delete OFF: the bytes stay in free pages.
    for name, table in (('devices.sqlite3', 'device_commands'), ('native-device.sqlite3', 'native_commands')):
        db = sqlite3.connect(data / name, isolation_level=None)
        db.execute('PRAGMA secure_delete=OFF')
        assert db.execute('PRAGMA secure_delete').fetchone()[0] == 0
        db.execute(f"DELETE FROM {table} WHERE device_id='dev-alice'")
        db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        db.close()
    assert b'quailmark' in blob(data, 'devices.sqlite3'), 'precondition: stale owner bytes on disk'
    purger(data, monkeypatch).purge('alice', requested_by='t')
    for prefix in ('devices.sqlite3', 'native-device.sqlite3'):
        content = blob(data, prefix)
        assert b'quailmark' not in content and creds['secret'].encode() not in content and b'dev-alice' not in content, prefix
        assert b'yakmark' in content
    for prefix in ('devices.sqlite3', 'native-device.sqlite3'):
        db = sqlite3.connect(data / prefix)
        assert db.execute('PRAGMA freelist_count').fetchone()[0] == 0
        db.close()


def test_race_command_journaled_between_journal_and_registry_purge(tmp_path, monkeypatch):
    data = mk(tmp_path)
    reg, creds = enroll(data, 'alice', 'quailmark')
    client = NativeClient(reg, 'alice', creds['device_id'], creds['secret'], FakeAdapter('lateark'),
                          data / 'native-device.sqlite3')
    envelope = reg.issue('alice', creds['device_id'], 'window.observe', {})
    stores = LocalDeviceStores(data)
    real = client.registry.command
    outcome = {}

    def command_then_purge(command_id):
        row = real(command_id)  # the client already passed the revoked check; the purge runs now
        t = threading.Thread(target=lambda: outcome.setdefault('counts', stores.delete_owner('alice')))
        t.start(); t.join()
        return row

    client.registry.command = command_then_purge
    with pytest.raises(PermissionError, match='being deleted'):
        client.execute(envelope)
    client.registry.command = real
    jr = sqlite3.connect(data / 'native-device.sqlite3')
    assert jr.execute('SELECT count(*) FROM native_commands').fetchone()[0] == 0
    jr.close()
    assert b'lateark' not in blob(data, 'native-device.sqlite3')
    # A later command cannot slip in either: the device is gone/revoked.
    with pytest.raises(PermissionError):
        client.execute(envelope)


def test_race_after_registry_delete_is_also_blocked(tmp_path):
    """Client passed the registry check, purge fully finished, then it tries to journal."""
    data = mk(tmp_path)
    reg, creds = enroll(data, 'alice', 'quailmark')
    client = NativeClient(reg, 'alice', creds['device_id'], creds['secret'], FakeAdapter('lateark'),
                          data / 'native-device.sqlite3')
    envelope = reg.issue('alice', creds['device_id'], 'window.observe', {})
    real_get = reg.get
    stale = real_get('alice', creds['device_id'])
    reg.get = lambda o, d: stale  # stale view captured before the purge
    real_cmd = reg.command

    def command_then_purge(cid):
        row = real_cmd(cid)
        LocalDeviceStores(data).delete_owner('alice')
        return row

    reg.command = command_then_purge
    with pytest.raises(PermissionError):
        client.execute(envelope)
    jr = sqlite3.connect(data / 'native-device.sqlite3')
    assert jr.execute('SELECT count(*) FROM native_commands').fetchone()[0] == 0
    jr.close()


def test_re_enrolled_device_after_purge_is_not_blocked(tmp_path):
    data = mk(tmp_path)
    reg, _creds = enroll(data, 'alice', 'quailmark')
    reg.db.close()
    LocalDeviceStores(data).delete_owner('alice')
    _reg2, _ = enroll(data, 'alice', 'newmark')  # same owner and device id, new pairing
    jr = sqlite3.connect(data / 'native-device.sqlite3')
    assert jr.execute('SELECT count(*) FROM native_commands').fetchone()[0] == 2
    jr.close()


def test_orphan_rows_of_this_owner_are_swept_and_others_kept(tmp_path):
    data = mk(tmp_path)
    _reg, _creds = enroll(data, 'alice', 'quailmark')
    enroll(data, 'bob', 'yakmark')
    stores = LocalDeviceStores(data)
    stores.delete_owner('alice')
    stores.finish_owner('alice')  # the deletion ledger does this once the step is recorded
    # An orphan for alice's device id appears later (pre-fix writer) and an unattributable one.
    jr = sqlite3.connect(data / 'native-device.sqlite3', isolation_level=None)
    jr.execute("INSERT INTO native_commands VALUES('c-orph','n-orph','dev-alice','started',NULL,'now')")
    jr.execute("INSERT INTO native_commands VALUES('c-unk','n-unk','dev-unknown','started',NULL,'now')")
    jr.close()
    counts = stores.delete_owner('alice')
    assert counts['native_commands'] == 1
    jr = sqlite3.connect(data / 'native-device.sqlite3')
    ids = sorted(r[0] for r in jr.execute('SELECT device_id FROM native_commands'))
    jr.close()
    assert ids == ['dev-bob', 'dev-bob', 'dev-unknown']  # no ownership proof for dev-unknown: kept, documented


def test_crash_after_registry_delete_resumes_with_correct_counts(tmp_path, monkeypatch):
    data = mk(tmp_path)
    reg, _ = enroll(data, 'alice', 'quailmark')
    enroll(data, 'bob', 'yakmark')
    reg.db.close()
    p = purger(data, monkeypatch)
    real = LocalDeviceStores._compact
    calls = {'n': 0}

    def crash_on_registry_compact(db):
        calls['n'] += 1
        if calls['n'] == 2:  # journal compacted, registry rows already deleted
            raise RuntimeError('simulated crash')
        return real(db)

    monkeypatch.setattr(LocalDeviceStores, '_compact', staticmethod(crash_on_registry_compact))
    with pytest.raises(RuntimeError, match='simulated crash'):
        p.purge('alice', requested_by='t')
    db = sqlite3.connect(data / 'devices.sqlite3')
    assert db.execute("SELECT count(*) FROM devices WHERE owner_id='alice'").fetchone()[0] == 0
    db.close()
    monkeypatch.setattr(LocalDeviceStores, '_compact', staticmethod(real))
    resumed = p.resume_incomplete()
    assert len(resumed) == 1 and resumed[0]['status'] == 'completed'
    assert resumed[0]['steps']['local_devices'] == {
        'device_pairings': 1, 'devices': 1, 'device_commands': 2, 'native_commands': 2}
    db = sqlite3.connect(data / 'devices.sqlite3')
    assert db.execute('SELECT count(*) FROM purge_progress').fetchone()[0] == 0
    db.close()
    for prefix in ('devices.sqlite3', 'native-device.sqlite3'):
        assert b'quailmark' not in blob(data, prefix) and b'yakmark' in blob(data, prefix)


def test_crash_between_journal_and_registry_delete_resumes_with_correct_counts(tmp_path, monkeypatch):
    data = mk(tmp_path)
    reg, _ = enroll(data, 'alice', 'quailmark')
    reg.db.close()
    p = purger(data, monkeypatch)
    real = LocalDeviceStores._sweep_journal
    state = {'n': 0}

    def sweep(self, jr, owner, keys):
        state['n'] += 1
        out = real(self, jr, owner, keys)
        if state['n'] == 1:
            raise RuntimeError('simulated crash after journal delete')
        return out

    monkeypatch.setattr(LocalDeviceStores, '_sweep_journal', sweep)
    with pytest.raises(RuntimeError):
        p.purge('alice', requested_by='t')
    resumed = p.resume_incomplete()
    assert resumed[0]['steps']['local_devices'] == {
        'device_pairings': 1, 'devices': 1, 'device_commands': 2, 'native_commands': 2}
    assert b'quailmark' not in blob(data, 'native-device.sqlite3') + blob(data, 'devices.sqlite3')
