import os
import shutil
import subprocess
import time

import pytest

from meemee.devices import DeviceRegistry
from meemee.native_device import NativeClient, WindowsAdapter, X11Adapter


def test_real_x11_window_control_pair_audit_replay_revoke(tmp_path):
    for binary in ('Xvfb', 'xdotool', 'xmessage', 'import'):
        if not shutil.which(binary):
            pytest.skip(f'{binary} unavailable')
    display = ':87'
    env = {**os.environ, 'DISPLAY': display}
    server = subprocess.Popen(['Xvfb', display, '-screen', '0', '800x600x24'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    window = None
    try:
        time.sleep(.5)
        window = subprocess.Popen(['xmessage','-name','MeemeeTest','-title','Meemee safe test','Test application'],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        time.sleep(.5)
        ident = subprocess.check_output(['xdotool','search','--name','Meemee safe test'], env=env,text=True).strip().splitlines()[0]
        registry = DeviceRegistry(tmp_path/'devices.db')
        pairing = registry.create_pairing('owner')
        creds = registry.pair(pairing['pairing_id'], pairing['code'],device_id='desktop',name='test',capabilities=NativeClient.manifest())
        adapter = X11Adapter(int(ident), display)
        client = NativeClient(registry,'owner',creds['device_id'],creds['secret'],adapter,tmp_path/'native.db')
        observe = registry.issue('owner','desktop','window.observe',{})
        result = client.execute(observe)
        assert result['title'] == 'Meemee safe test'
        assert result['observed_at']
        screenshot = tmp_path/'native-window.png'
        adapter.screenshot(screenshot)
        assert screenshot.stat().st_size > 100
        change = registry.issue('owner','desktop','window.set_title',{'title':'Meemee action done'})
        assert client.execute(change)['title'] == 'Meemee action done'
        assert adapter.observe()['title'] == 'Meemee action done'
        assert registry.command(change['command_id'])['status'] == 'completed'
        with pytest.raises(ValueError):
            NativeClient(registry,'owner','desktop',creds['secret'],adapter,tmp_path/'native.db').execute(change)
        blocked = registry.issue('owner','desktop','window.set_title',{'title':'should not happen'})
        registry.revoke('owner','desktop')
        with pytest.raises(PermissionError): client.execute(blocked)
        assert adapter.observe()['title'] == 'Meemee action done'
        shutil.copy(screenshot, 'evidence/native-window.png')
    finally:
        if window: window.terminate(); window.wait(timeout=5)
        server.terminate(); server.wait(timeout=5)


def test_windows_fails_explicitly_off_platform():
    if os.name != 'nt':
        with pytest.raises(OSError): WindowsAdapter(1)
