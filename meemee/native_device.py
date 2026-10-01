"""Real bounded native window adapters, not a desktop simulator."""
from __future__ import annotations

import ctypes
import json
import os
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from .device_protocol import verify_command


class OSAdapter(Protocol):
    def observe(self) -> dict: ...
    def set_title(self, title: str) -> dict: ...
    def screenshot(self, destination: Path) -> None: ...


def _title(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 240 or any(ord(c) < 32 for c in value):
        raise ValueError('title must be 1..240 printable characters')
    return value


class X11Adapter:
    def __init__(self, window_id: int, display: str | None = None):
        if os.name != 'posix' or window_id <= 0:
            raise OSError('Linux/X11 and a positive target window required')
        self.window_id = str(window_id)
        self.env = dict(os.environ)
        if display:
            self.env['DISPLAY'] = display
        if not self.env.get('DISPLAY'):
            raise OSError('DISPLAY required; Wayland is not supported')

    def _run(self, *args):
        return subprocess.run(args, env=self.env, text=True, capture_output=True,
                              timeout=10, check=True).stdout.strip()

    def observe(self):
        title = self._run('xdotool', 'getwindowname', self.window_id)
        geometry = self._run('xdotool', 'getwindowgeometry', '--shell', self.window_id)
        return {'title': title, 'geometry': geometry,
                'observed_at': datetime.now(timezone.utc).isoformat(), 'platform': 'x11'}

    def set_title(self, title):
        self._run('xdotool', 'set_window', '--name', _title(title), self.window_id)
        result = self.observe()
        if result['title'] != title:
            raise RuntimeError('window title readback did not match')
        return result

    def screenshot(self, destination):
        # Window-only capture, never the whole desktop.
        self._run('import', '-window', self.window_id, str(destination.resolve()))


class WindowsAdapter:
    """Win32 window observation and bounded title mutation. Untested off Windows."""
    def __init__(self, window_id: int):
        if os.name != 'nt':
            raise OSError('Windows adapter requires Windows')
        from ctypes import wintypes
        self.window = wintypes.HWND(window_id)
        self.user32 = ctypes.WinDLL('user32', use_last_error=True)
        self.user32.IsWindow.argtypes = [wintypes.HWND]
        self.user32.IsWindow.restype = wintypes.BOOL
        self.user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        self.user32.GetWindowTextLengthW.restype = ctypes.c_int
        self.user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        self.user32.GetWindowTextW.restype = ctypes.c_int
        self.user32.SetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
        self.user32.SetWindowTextW.restype = wintypes.BOOL
        self.user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        self.user32.GetWindowRect.restype = wintypes.BOOL
        self.rect_type = wintypes.RECT
        self._check()

    def _check(self):
        if not self.user32.IsWindow(self.window):
            raise OSError('target window is absent')

    def observe(self):
        self._check()
        buffer = ctypes.create_unicode_buffer(self.user32.GetWindowTextLengthW(self.window) + 1)
        self.user32.GetWindowTextW(self.window, buffer, len(buffer))
        rect = self.rect_type()
        if not self.user32.GetWindowRect(self.window, ctypes.byref(rect)):
            raise ctypes.WinError(ctypes.get_last_error())
        return {'title': buffer.value, 'geometry': [rect.left, rect.top, rect.right, rect.bottom],
                'observed_at': datetime.now(timezone.utc).isoformat(), 'platform': 'windows'}

    def set_title(self, title):
        self._check()
        if not self.user32.SetWindowTextW(self.window, _title(title)):
            raise ctypes.WinError(ctypes.get_last_error())
        result = self.observe()
        if result['title'] != title:
            raise RuntimeError('target denied window title change')
        return result

    def screenshot(self, destination):
        from PIL import ImageGrab
        result = self.observe()
        # Capture only the target rectangle; occluding windows may be visible.
        ImageGrab.grab(bbox=tuple(result['geometry'])).save(destination)


class NativeClient:
    """Local paired client with a durable non-retry execution journal."""
    def __init__(self, registry, owner, device_id, secret_hex, adapter: OSAdapter, journal: Path):
        self.registry, self.owner, self.device_id = registry, owner, device_id
        self.secret, self.adapter = bytes.fromhex(secret_hex), adapter
        journal.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(journal, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000;
            CREATE TABLE IF NOT EXISTS native_commands(command_id TEXT PRIMARY KEY,
                nonce TEXT UNIQUE NOT NULL, device_id TEXT NOT NULL, status TEXT NOT NULL,
                result TEXT, observed_at TEXT NOT NULL);
        ''')

    @staticmethod
    def manifest():
        return {'window.observe': {'version': 1, 'risk': 'read', 'scope': 'one pinned window'},
                'window.set_title': {'version': 1, 'risk': 'write', 'scope': 'one pinned window'}}

    def execute(self, envelope):
        device = self.registry.get(self.owner, self.device_id)
        if not device or device['revoked_at']:
            raise PermissionError('device unpaired or revoked')
        command = verify_command(envelope, self.secret, expected_device_id=self.device_id,
                                 allowed_capabilities=set(device['manifest']) & set(self.manifest()))
        issued = self.registry.command(command.command_id)
        if not issued or issued['device_id'] != self.device_id or issued['envelope'] != envelope or issued['status'] != 'issued':
            raise ValueError('command not currently issued by paired registry')
        expected = {} if command.capability == 'window.observe' else {'title': command.arguments.get('title')}
        if command.arguments != expected:
            raise ValueError('unsupported command arguments')
        if command.capability == 'window.set_title':
            _title(command.arguments['title'])
        try:
            self.db.execute('INSERT INTO native_commands VALUES(?,?,?,\'started\',NULL,?)',
                            (command.command_id, command.nonce, self.device_id, datetime.now(timezone.utc).isoformat()))
        except sqlite3.IntegrityError as exc:
            raise ValueError('command/nonce already attempted; reconcile, never blindly retry') from exc
        try:
            result = self.adapter.observe() if command.capability == 'window.observe' else self.adapter.set_title(command.arguments['title'])
            self.db.execute("UPDATE native_commands SET status='completed',result=? WHERE command_id=?", (json.dumps(result, sort_keys=True), command.command_id))
            self.registry.complete(command.command_id, result=result)
            return result
        except Exception:
            # A started/uncertain record forbids a second action after an interruption.
            self.db.execute("UPDATE native_commands SET status='uncertain' WHERE command_id=?", (command.command_id,))
            raise
