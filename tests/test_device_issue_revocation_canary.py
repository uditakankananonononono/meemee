"""Independent revocation must serialize with command authorization/publication."""
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from meemee import devices
from meemee.devices import DeviceRegistry


def test_revoke_cannot_commit_before_authorized_issue_publication(tmp_path, monkeypatch):
    registries = [DeviceRegistry(tmp_path / 'd.db'), DeviceRegistry(tmp_path / 'd.db')]
    pairing = registries[0].create_pairing('owner')
    registries[0].pair(pairing['pairing_id'], pairing['code'], device_id='d', name='fixture',
                       capabilities={'echo': {}})
    entered = threading.Event()
    original = devices.make_command

    def paused_command(*args, **kwargs):
        entered.set()
        time.sleep(0.15)
        return original(*args, **kwargs)

    monkeypatch.setattr(devices, 'make_command', paused_command)
    with ThreadPoolExecutor(max_workers=2) as pool:
        issued = pool.submit(registries[0].issue, 'owner', 'd', 'echo', {})
        assert entered.wait(2)
        revoked = pool.submit(registries[1].revoke, 'owner', 'd')
        envelope = issued.result(timeout=3)
        assert revoked.result(timeout=3)
    command = registries[0].command(envelope['command_id'])
    device = registries[1].get('owner', 'd')
    assert command['created_at'] <= device['revoked_at']
