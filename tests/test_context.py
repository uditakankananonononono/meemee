import hashlib
import hmac
import time
from pathlib import Path

import pytest

from meemee.context import ContextRecord, ContextStore, verify_webhook_signature


def test_source_ledger_dedup_cursor_permissions_and_assembly(tmp_path: Path):
 s=ContextStore(tmp_path/'context.db');s.register_source('u','mail','signed_webhook',{'topic':'mail'})
 record=ContextRecord('u','mail','m1','event','Project update','Launch moved to Friday','2026-09-22T10:00:00+00:00',{'message_id':'m1'},'private','cursor-1')
 assert s.ingest(record);assert not s.ingest(record);assert s.cursor('u','mail')=='cursor-1'
 assert s.search('other','Launch')==[]
 assembled=s.assemble('u','Launch');assert assembled['records'][0]['provenance']['message_id']=='m1'

def test_webhook_signature_is_constant_time_timestamped():
 body=b'{"id":"x"}';stamp=str(int(time.time()));secret='s'*32;sig=hmac.new(secret.encode(),stamp.encode()+b'.'+body,hashlib.sha256).hexdigest()
 verify_webhook_signature(secret,body,stamp,'sha256='+sig)
 with pytest.raises(ValueError):verify_webhook_signature(secret,body,stamp,'bad')
 with pytest.raises(ValueError):verify_webhook_signature(secret,body,'1','sha256='+sig)
