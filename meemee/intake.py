"""Autonomous, bounded local RSS/ICS snapshot intake with durable recovery."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import asdict
from pathlib import Path
from xml.etree.ElementTree import ParseError

from .connectors import ICSConnector, RSSConnector
from .context import ContextRecord, ContextStore
from .goals import GoalStore
from .monitors import MonitorStore
from .source_health import SourceHealthStore


class LocalRSS(RSSConnector):
    def __init__(self, path: Path):
        super().__init__('local_rss', str(path))
        self.path = path

    def read(self):
        with self.path.open('rb') as stream:
            payload = stream.read(2_000_001)
        if len(payload) > 2_000_000:
            raise ValueError('feed exceeds 2 MB')
        return payload


class LocalICS(ICSConnector, LocalRSS):
    def __init__(self, path: Path):
        LocalRSS.__init__(self, path)
        self.name = 'local_ics'

    def read(self):
        payload = LocalRSS.read(self)
        if not payload.lstrip().startswith(b'BEGIN:VCALENDAR') or b'END:VCALENDAR' not in payload:
            raise ValueError('not a complete ICS calendar')
        if payload.count(b'BEGIN:VEVENT') != payload.count(b'END:VEVENT'):
            raise ValueError('incomplete ICS events')
        return payload


class IntakeService:
    def __init__(self, data_dir: Path, source_root: Path):
        self.root = source_root.resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError('source root must be a directory')
        self.context = ContextStore(data_dir / 'context.sqlite3')
        self.monitors = MonitorStore(data_dir / 'monitors.sqlite3')
        self.goals = GoalStore(data_dir / 'goals.sqlite3')
        self.health = SourceHealthStore(data_dir / 'source-health.sqlite3')
        self.db = sqlite3.connect(data_dir / 'intake.sqlite3', isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            PRAGMA journal_mode=WAL;
            PRAGMA busy_timeout=5000;
            CREATE TABLE IF NOT EXISTS intake_snapshots(owner TEXT, source TEXT, fingerprint TEXT,
                records TEXT NOT NULL, config TEXT NOT NULL, pending INTEGER NOT NULL,
                next_poll REAL NOT NULL, PRIMARY KEY(owner,source));
        ''')

    def tick(self, *, force=False):
        counts = {'ok': 0, 'failed': 0}
        # One writer serializes fetch/recovery across local workers. Bounded file I/O only.
        self.db.execute('BEGIN IMMEDIATE')
        try:
            for source in self.context.sources():
                if source is None or source['config'].get('enabled', True) is not True:
                    continue
                owner, ident = source['owner_id'], source['source_id']
                row = self.db.execute('SELECT * FROM intake_snapshots WHERE owner=? AND source=?', (owner, ident)).fetchone()
                if row and not row['pending'] and not force and row['next_poll'] > time.time():
                    continue
                started = time.monotonic()
                try:
                    config = json.dumps(source['config'], sort_keys=True)
                    if row and row['pending'] and row['config'] == config:
                        records = [ContextRecord(**r) for r in json.loads(row['records'])]
                        fingerprint = row['fingerprint']
                    else:
                        relative = Path(source['config']['path'])
                        if relative.is_absolute():
                            raise ValueError('use a source-root-relative path')
                        path = (self.root / relative).resolve(strict=True)
                        if not path.is_relative_to(self.root) or not path.is_file():
                            raise ValueError('source escapes allowed root')
                        connector_cls = {'local_rss': LocalRSS, 'local_ics': LocalICS}.get(source['connector'])
                        if connector_cls is None:
                            raise ValueError('network/unconfigured connector not permitted')
                        records = connector_cls(path).fetch(owner, ident)
                        if len(records) > 1000 or len({r.external_id for r in records}) != len(records):
                            raise ValueError('snapshot too large or duplicate ids')
                        # Stable content fingerprint, independent of fallback parser clock.
                        fingerprint = hashlib.sha256(json.dumps([(r.external_id, r.title, r.content, r.metadata) for r in records], sort_keys=True).encode()).hexdigest()
                    interval = max(5, min(int(source['config'].get('interval_seconds', 60)), 86400))
                    encoded = json.dumps([asdict(r) for r in records], sort_keys=True)
                    self.db.execute('INSERT OR REPLACE INTO intake_snapshots VALUES(?,?,?,?,?,1,?)',
                                    (owner, ident, fingerprint, encoded, config, time.time() + interval))
                    # Commit the intent before crossing database boundaries. Replays are idempotent.
                    self.db.execute('COMMIT')
                    self.db.execute('BEGIN IMMEDIATE')
                    live = self.context.source(owner, ident)
                    if live is None or live['connector'] != source['connector'] or json.dumps(live['config'], sort_keys=True) != config or live['config'].get('enabled', True) is not True:
                        self.db.execute('DELETE FROM intake_snapshots WHERE owner=? AND source=?', (owner, ident))
                        continue
                    self.context.reconcile_snapshot(owner, ident, records, fingerprint)
                    for r in records:
                        digest = hashlib.sha256((r.title + '\0' + r.content).encode()).hexdigest()
                        self.monitors.accept_event(owner, ident, r.external_id + ':' + digest,
                                                   {'title': r.title, 'content': r.content, 'kind': r.kind, 'external_id': r.external_id, **(r.metadata or {})})
                    self.monitors.expire()
                    self.monitors.dispatch()
                    if not row or row['fingerprint'] != fingerprint or row['pending']:
                        for goal in self.goals.list(owner, 'blocked'):
                            steps = goal['context'].get('steps', [])
                            self.goals.init_execution()
                            progress = self.goals.db.execute('SELECT step FROM agency_progress WHERE goal_id=?', (goal['id'],)).fetchone()
                            index = progress[0] if progress else 0
                            if index < len(steps) and steps[index].get('kind') == 'wait' and steps[index].get('source_id') == ident:
                                self.goals.wake(owner, goal['id'])
                    self.db.execute('UPDATE intake_snapshots SET pending=0 WHERE owner=? AND source=?', (owner, ident))
                    self.health.record(owner, ident, ok=True, cursor=fingerprint, latency_ms=(time.monotonic()-started)*1000)
                    counts['ok'] += 1
                except (ValueError, OSError, KeyError, TypeError, ParseError) as exc:
                    # Error types only, never a path, URL, credential or raw payload.
                    self.health.record(owner, ident, ok=False, error=type(exc).__name__)
                    counts['failed'] += 1
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK')
            raise
        return counts
