"""Exercise real HTTP monitor evaluation and persisted readback, without a model.

Run from repository root: uv run --locked python examples/monitor_event_walkthrough.py
Only temporary SQLite state and loopback HTTP are used. No external effects.
"""
from __future__ import annotations

import base64
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def start(root, port, token):
    env = {**os.environ, 'MEEMEE_DATA_DIR': str(root), 'MEEMEE_API_TOKEN': token,
           'MEEMEE_MODEL_BASE_URL': 'http://127.0.0.1:1/v1', 'MEEMEE_HF_FALLBACK': 'false',
           'MEEMEE_DATABASE_URL': '', 'MEEMEE_PERSISTENCE_BACKEND': 'sqlite',
           'MEEMEE_VAULT_KEY': base64.urlsafe_b64encode(bytes(range(32))).decode()}
    process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'meemee.api:app', '--host', '127.0.0.1', '--port', str(port)], env=env,
                               stdout=subprocess.DEVNULL)
    base = f'http://127.0.0.1:{port}'
    for _ in range(150):
        if process.poll() is not None:
            raise RuntimeError('fixture API exited during startup')
        try:
            if httpx.get(base + '/health', timeout=1).status_code == 200:
                return process, base
        except httpx.HTTPError:
            pass
        time.sleep(0.05)
    process.terminate()
    process.wait(timeout=10)
    raise RuntimeError('fixture API startup timed out')


def stop(process):
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def main():
    with tempfile.TemporaryDirectory(prefix='meemee-monitor-') as directory:
        token = 'local-walkthrough-fixture'
        headers = {'Authorization': 'Bearer ' + token}
        process, base = start(Path(directory), free_port(), token)
        try:
            with httpx.Client(base_url=base, headers=headers, timeout=10) as client:
                response = client.post('/v1/monitors', json={'name':'Supplied price below 10','source_id':'fixture-shop',
                    'field':'price','operator':'lt','expected':10,'max_fires':1})
                response.raise_for_status()
                ident = response.json()['id']
                observations = []
                for price in [15, 5, 4]:
                    response = client.post('/v1/monitors/evaluate', json={'source_id':'fixture-shop','event':{'price':price}})
                    response.raise_for_status()
                    observations.append(response.json()['fired'])
                assert observations == [[], [ident], []], observations
        finally:
            stop(process)
        process, base = start(Path(directory), free_port(), token)
        try:
            with httpx.Client(base_url=base, headers=headers, timeout=10) as client:
                response = client.get('/v1/monitors')
                response.raise_for_status()
                row = next(item for item in response.json()['monitors'] if item['id'] == ident)
                response = client.get('/v1/monitors/' + ident + '/events')
                response.raise_for_status()
                events = response.json()['events']
                assert row['status'] == 'completed' and row['fire_count'] == 1
                assert [item['kind'] for item in events] == ['created', 'triggered']
                assert events[-1]['payload'] == {'price':5}
                print(json.dumps({'mode':'temporary SQLite + real loopback HTTP, no model', 'restarted':True,
                    'observations':observations,'status':row['status'],'fire_count':row['fire_count'],'event_kinds':[item['kind'] for item in events],
                    'trigger_payload':events[-1]['payload'],'external_truth_verified':False,'notifications_sent':False}, indent=2))
        finally:
            stop(process)


if __name__ == '__main__':
    main()
