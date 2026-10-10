"""Durable check-in acquisition, send intent and generation fencing.

Legacy running rows have unknown delivery evidence and are never auto-retried.
This protocol cannot make a provider send transactional with our database.
"""
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from ..sensitive import scrub_text


def lease_clock(now=None):
    instant = datetime.now(timezone.utc) if now is None else now
    if not isinstance(instant, datetime) or instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError('check-in clock requires a timezone')
    return instant.astimezone(timezone.utc)


class LostCheckinClaim(RuntimeError):
    """The generation no longer owns this check-in. Never send or retry it."""


class CheckinLeaseMixin:
    _checkin_pg = False

    @contextmanager
    def _lease_transaction(self):
        if self._checkin_pg:
            with self.db.transaction() as c:
                yield _Queries(c, True)
        else:
            with self.lock:
                self.db.execute('BEGIN IMMEDIATE')
                try:
                    yield _Queries(self.db, False)
                    self.db.commit()
                except BaseException:
                    self.db.rollback()
                    raise

    def recover_checkin_claims(self, now=None):
        """Retry only expired proven-unstarted claims, quarantine all ambiguity.

        Legacy running rows without a lease are stopped, never guessed unstarted.
        Counts describe transitions, not delivery outcomes.
        """
        clock=lease_clock(now).isoformat();counts={'requeued':0,'quarantined':0,'exhausted':0}
        with self._lease_transaction() as c:
            rows=c.execute("SELECT * FROM companion_checkins WHERE status='running' AND (lease_until IS NULL OR lease_until<=?) ORDER BY id"+c.for_update,(clock,)).fetchall()
            for row in rows:
                fresh=lease_clock(now);clock=fresh.isoformat()
                if row['lease_until'] is not None and lease_clock(datetime.fromisoformat(str(row['lease_until'])))>fresh:
                    continue
                retry=(row['claim_token'] is not None and row['delivery_state']=='not_started')
                status='queued' if retry and row['attempts']<row['max_attempts'] else 'failed'
                key='requeued' if status=='queued' else 'exhausted' if retry else 'quarantined'
                c.execute("UPDATE companion_checkins SET status=?,delivery_state=?,last_error=?,updated_at=? WHERE id=? AND status='running'",(status,'not_started' if retry else 'unknown','expired claim before send' if retry else 'delivery outcome unknown: expired or legacy claim',clock,row['id']))
                counts[key]+=1
        return counts

    def claim_checkin_fenced(self, now=None, *, lease_seconds=300):
        if type(lease_seconds) is not int or not 3<=lease_seconds<=3600:
            raise ValueError('lease seconds must be an integer between3 and3600')
        lease_clock(now)  # Validate explicit clocks before I/O, not runtime timestamps.
        with self._lease_transaction() as c:
            clock=lease_clock(now).isoformat()
            # Exhausted legacy queued jobs must not get one extra send.
            c.execute("UPDATE companion_checkins SET status='failed',last_error='check-in attempt budget exhausted',updated_at=? WHERE status='queued' AND attempts>=max_attempts",(clock,))
            row=c.execute("SELECT * FROM companion_checkins WHERE status='queued' AND due_at<=? AND attempts<max_attempts ORDER BY due_at,id LIMIT 1"+c.claim_lock,(clock,)).fetchone()
            if row is None:return None
            instant=lease_clock(now);clock=instant.isoformat();until=(instant+timedelta(seconds=lease_seconds)).isoformat()
            if lease_clock(datetime.fromisoformat(str(row['due_at'])))>instant:return None
            token=uuid.uuid4().hex
            c.execute("UPDATE companion_checkins SET status='running',attempts=attempts+1,claim_generation=claim_generation+1,claim_token=?,claimed_at=?,lease_until=?,delivery_state='not_started',updated_at=? WHERE id=? AND status='queued'",(token,clock,until,clock,row['id']))
            return dict(c.execute('SELECT * FROM companion_checkins WHERE id=?',(row['id'],)).fetchone())

    def _owned_claim(self,c,claim,now=None):
        if type(claim) is not dict or not all(k in claim for k in ('id','user_id','claim_token','claim_generation')) or type(claim['claim_token']) is not str or not claim['claim_token'] or type(claim['claim_generation']) is not int or claim['claim_generation']<1:
            raise LostCheckinClaim('check-in claim lost')
        row=c.execute("SELECT * FROM companion_checkins WHERE id=? AND user_id=? AND claim_token=? AND claim_generation=? AND status='running'"+c.for_update,(claim['id'],claim['user_id'],claim['claim_token'],claim['claim_generation'])).fetchone()
        if row is None:raise LostCheckinClaim('check-in claim lost')
        self._validate_claim_time(row,now)
        return row

    def _validate_claim_time(self,row,now=None):
        # Called only after authoritative row locks are acquired. Runtime None
        # reads a fresh clock here; explicit test clocks deliberately stay fixed.
        instant=lease_clock(now)
        expiry=row['lease_until'];claimed=row['claimed_at']
        if expiry is None or claimed is None:
            raise LostCheckinClaim('check-in claim lost')
        expiry=lease_clock(datetime.fromisoformat(str(expiry)))
        claimed=lease_clock(datetime.fromisoformat(str(claimed)))
        if not claimed<=instant<expiry:raise LostCheckinClaim('check-in claim expired')
        return instant

    def renew_checkin_claim(self,claim,now=None,*,lease_seconds=300):
        if type(lease_seconds) is not int or not 3<=lease_seconds<=3600:raise ValueError('invalid lease seconds')
        with self._lease_transaction() as c:
            row=self._owned_claim(c,claim,now)
            instant=self._validate_claim_time(row,now);clock=instant.isoformat()
            c.execute('UPDATE companion_checkins SET lease_until=?,updated_at=? WHERE id=?',((instant+timedelta(seconds=lease_seconds)).isoformat(),clock,claim['id']))
        return True

    def _permission_current(self,c,row,instant):
        # PG profile lock and SQLite write transaction fence current preferences
        # at intent/local commit. Revocation after commit cannot unsend a provider call.
        if self._checkin_pg:
            user=c.execute('SELECT * FROM companion_users WHERE user_id=? FOR SHARE',(row['user_id'],)).fetchone()
            from .models import UserProfile
            p=UserProfile(**{k:self._profile(user)[k] for k in ('user_id','display_name','timezone','persona','checkins')}) if user else None
        else:p=self.profile(row['user_id'])
        from .checkins import in_quiet_hours
        return bool(p and p.checkins.enabled and p.checkins.channel==row['channel'] and p.checkins.address==row['address'] and not(p.checkins.quiet_hours and in_quiet_hours(instant.astimezone(p.tz()),p.checkins.quiet_hours)))

    def start_checkin_delivery(self,claim,now=None):
        with self._lease_transaction() as c:
            row=self._owned_claim(c,claim,now)
            instant=self._validate_claim_time(row,now);clock=instant.isoformat()
            if row['delivery_state']!='not_started':raise LostCheckinClaim('check-in send intent already used')
            allowed=self._permission_current(c,row,instant)
            allowed=allowed and self._destination_verified_locked(c,row['user_id'],row['channel'],row['address'],now)
            instant=self._validate_claim_time(row,now);clock=instant.isoformat()
            if not allowed:
                c.execute("UPDATE companion_checkins SET status='cancelled',updated_at=? WHERE id=?",(clock,row['id']))
                return False
            c.execute("UPDATE companion_checkins SET delivery_state='started',updated_at=? WHERE id=?",(clock,row['id']))
        return True

    def finish_checkin_claim(self,claim,message,now=None):
        with self._lease_transaction() as c:
            row=self._owned_claim(c,claim,now)
            clock=self._validate_claim_time(row,now).isoformat()
            if row['delivery_state']!='started':raise LostCheckinClaim('check-in has no send intent')
            c.execute("UPDATE companion_checkins SET status='done',delivery_state='accepted',message=?,last_error=NULL,updated_at=? WHERE id=?",(scrub_text(message),clock,row['id']))

    def fail_checkin_claim(self,claim,error,now=None,*,proven_not_delivered=False):
        with self._lease_transaction() as c:
            row=self._owned_claim(c,claim,now)
            clock=self._validate_claim_time(row,now).isoformat()
            safe=row['delivery_state']=='not_started' or proven_not_delivered
            status='queued' if safe and row['attempts']<row['max_attempts'] else 'failed'
            c.execute('UPDATE companion_checkins SET status=?,delivery_state=?,last_error=?,updated_at=? WHERE id=?',(status,'not_started' if safe else 'unknown',scrub_text(error)[:500],clock,row['id']))
        return status

    def unknown_checkin_claim(self,claim,error,now=None):
        with self._lease_transaction() as c:
            row=self._owned_claim(c,claim,now)
            clock=self._validate_claim_time(row,now).isoformat()
            c.execute("UPDATE companion_checkins SET status='failed',delivery_state='unknown',last_error=?,updated_at=? WHERE id=?",(scrub_text(error)[:500],clock,claim['id']))
        return True

    def cancel_checkin_claim(self,claim,now=None):
        with self._lease_transaction() as c:
            row=self._owned_claim(c,claim,now)
            clock=self._validate_claim_time(row,now).isoformat()
            c.execute("UPDATE companion_checkins SET status='cancelled',updated_at=? WHERE id=?",(clock,claim['id']))
        return True


class _Queries:
    def __init__(self,db,pg):self.db=db;self.pg=pg
    @property
    def for_update(self):return ' FOR UPDATE' if self.pg else ''
    @property
    def for_share(self):return ' FOR SHARE' if self.pg else ''
    @property
    def claim_lock(self):return ' FOR UPDATE SKIP LOCKED' if self.pg else ''
    def execute(self,sql,args=()):
        if self.pg:
            sql=sql.replace('companion_destination_challenges','meemee_companion_destination_challenges').replace('companion_destination_grants','meemee_companion_destination_grants').replace('companion_checkins','meemee_companion_checkins').replace('companion_users','meemee_companion_users').replace('?','%s')
        return self.db.execute(sql,args)
