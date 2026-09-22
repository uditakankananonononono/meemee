import base64

import httpx
import pytest
import respx

from meemee.connectors import GmailConnector


def test_gmail_requires_connection():
    with pytest.raises(RuntimeError, match="OAuth"):
        GmailConnector("").fetch("owner", "gmail")


@respx.mock
def test_gmail_reads_and_normalizes_messages():
    root="https://gmail.googleapis.com/gmail/v1/users/me"
    respx.get(root+"/messages", params={"maxResults":"100","q":"in:inbox"}).mock(return_value=httpx.Response(200,json={"messages":[{"id":"m1"}]}))
    payload=base64.urlsafe_b64encode(b"Yes, ship the release.").decode().rstrip("=")
    respx.get(root+"/messages/m1", params={"format":"full"}).mock(return_value=httpx.Response(200,json={"id":"m1","threadId":"t1","internalDate":"1700000000000","payload":{"headers":[{"name":"Subject","value":"Re: release"},{"name":"From","value":"u@example.com"}],"body":{"data":payload}}}))
    rows=GmailConnector("token").fetch("owner","gmail")
    assert rows[0].content=="Yes, ship the release."
    assert rows[0].provenance["thread_id"]=="t1"
