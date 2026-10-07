"""One pairing code must not create two devices across registry handles."""
import hmac
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from meemee.devices import DeviceRegistry


def test_one_pairing_has_one_device_winner(tmp_path, monkeypatch):
    registries = [DeviceRegistry(tmp_path / 'd.db'), DeviceRegistry(tmp_path / 'd.db')]
    pairing = registries[0].create_pairing('fixture-owner')
    original = hmac.compare_digest

    def delayed(a, b):
        time.sleep(0.05)
        return original(a, b)

    monkeypatch.setattr(hmac, 'compare_digest', delayed)
    ready = threading.Barrier(2)

    def pair(i):
        ready.wait()
        try:
            registries[i].pair(pairing['pairing_id'], pairing['code'], device_id=str(i), name='fixture', capabilities={'fixture':{}})
            return True
        except ValueError:
            return False

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(pair, range(2)))
    assert sum(results) == 1
    assert registries[0].db.execute('SELECT count(*) FROM devices').fetchone()[0] == 1
