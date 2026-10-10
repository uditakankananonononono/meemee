"""Real-clock permission regression: all locks must precede quiet-hour decisions."""
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone

from meemee.companion.models import CheckInPreferences, QuietHours, UserProfile
from meemee.companion.store import CompanionStore


def seed(store, boundary, name):
    quiet = QuietHours(start=boundary.strftime('%H:%M'),
                       end=(boundary + timedelta(minutes=1)).strftime('%H:%M'))
    address = 'https://example.com/receiver'
    store.upsert_user(UserProfile(user_id=name, display_name=name, timezone='UTC',
        checkins=CheckInPreferences(enabled=True, channel='webhook', address=address,
                                   quiet_hours=quiet)))
    ch, nonce = store.create_destination_challenge(name, address)
    grant = store.consume_destination_challenge(name, ch['id'], nonce)
    store.schedule_checkin(name, datetime.now(timezone.utc)-timedelta(seconds=1), name,
                           'webhook', address)
    claim = store.claim_checkin_fenced(lease_seconds=300)
    return grant, claim


def test_real_clock_grant_and_profile_wait_into_quiet_both_backends(tmp_path):
    """Four genuine competing locks wait over the same real UTC minute boundary.

    SQLite grant/profile locks are the database-wide BEGIN IMMEDIATE lock.
    PostgreSQL exercises each row-lock independently. No clock monkeypatch.
    """
    import pgserver

    from meemee.companion.checkins import in_quiet_hours
    from meemee_persist_pg import Database, MigrationStore
    from meemee_persist_pg.companion import CompanionStore as PG
    server = pgserver.get_server(tmp_path/'pg', cleanup_mode='stop')
    db = Database(server.get_uri(), max_size=12)
    MigrationStore(db).apply()
    handles = []
    try:
        with db.transaction() as c:
            print('ACTUAL_PG', c.execute('SELECT version() AS v').fetchone()['v'], flush=True)
        now = datetime.now(timezone.utc)
        # Avoid setup straddling a boundary: reserve at least 8s before quiet.
        boundary = now.replace(second=0, microsecond=0)+timedelta(minutes=1)
        if (boundary-now).total_seconds() < 8:
            boundary += timedelta(minutes=1)
        cases = []
        for backend in ('sqlite', 'pg'):
            for lock in ('grant', 'profile'):
                name = backend+'_'+lock
                if backend == 'sqlite':
                    path = tmp_path/(name+'.db')
                    s = CompanionStore(path); other = CompanionStore(path)
                    s.db.execute('PRAGMA busy_timeout=90000')
                    other.db.execute('PRAGMA busy_timeout=90000')
                    handles.extend([s.db, other.db])
                else:
                    s = PG(db); other = PG(db)
                grant, claim = seed(s, boundary, name)
                cases.append((backend, lock, s, other, grant, claim))
        with ThreadPoolExecutor(4) as pool:
            with ExitStack() as locks:
                futures = []
                for backend, lock, s, other, grant, claim in cases:
                    c = locks.enter_context(s._lease_transaction())
                    if lock == 'grant':
                        c.execute('SELECT id FROM companion_destination_grants WHERE id=?'+c.for_update,
                                  (grant['id'],)).fetchone()
                    else:
                        c.execute('SELECT user_id FROM companion_users WHERE user_id=?'+c.for_update,
                                  (claim['user_id'],)).fetchone()
                    futures.append(pool.submit(other.start_checkin_delivery, claim))
                time.sleep(.3)
                assert not any(f.done() for f in futures)
                print('REAL_QUIET_BOUNDARY', boundary.isoformat(), flush=True)
                time.sleep(max(0, (boundary-datetime.now(timezone.utc)).total_seconds())+.2)
            results = [f.result(timeout=10) for f in futures]
        invalid = []
        for (backend, lock, s, other, grant, claim), result in zip(cases, results):
            quiet = s.profile(claim['user_id']).checkins.quiet_hours
            row = s.list_checkins(claim['user_id'])[0]
            print('REAL_WAIT_RESULT', backend, lock, datetime.now(timezone.utc).isoformat(),
                  result, row['status'], row['delivery_state'], flush=True)
            assert in_quiet_hours(datetime.now(timezone.utc), quiet)
            if result is not False or row['status'] != 'cancelled' or row['delivery_state'] != 'not_started' or row['message'] is not None:
                invalid.append((backend, lock, row))
        assert not invalid, invalid
    finally:
        for h in handles: h.close()
        db.close(); server.cleanup()


def test_all_lock_lookups_precede_permission_clock(tmp_path, monkeypatch):
    """Supplement only: deterministic clock switches at the last grant lookup."""
    import meemee.companion.checkin_leases as leases
    s = CompanionStore(tmp_path/'clock.db')
    try:
        before = datetime(2026, 1, 1, 12, 59, 59, tzinfo=timezone.utc)
        after = before + timedelta(seconds=2)
        quiet = QuietHours(start='13:00', end='14:00')
        s.upsert_user(UserProfile(user_id='u', display_name='U', checkins=CheckInPreferences(
            enabled=True, channel='webhook', address='https://example.com', quiet_hours=quiet)))
        ch, nonce = s.create_destination_challenge('u', 'https://example.com', before)
        s.consume_destination_challenge('u', ch['id'], nonce, before)
        s.schedule_checkin('u', before, 'one', 'webhook', 'https://example.com')
        a = s.claim_checkin_fenced(before)
        clock = [before]
        original = s._destination_grants_locked
        def last_lock(*args):
            rows = original(*args)
            clock[0] = after
            return rows
        monkeypatch.setattr(s, '_destination_grants_locked', last_lock)
        monkeypatch.setattr(leases, 'lease_clock', lambda now=None: now or clock[0])
        assert s.start_checkin_delivery(a) is False
        assert s.list_checkins('u')[0]['delivery_state'] == 'not_started'
    finally:
        s.db.close()
