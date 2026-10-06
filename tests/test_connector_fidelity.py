import base64
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
import respx

from meemee.connectors import GmailConnector, ICSConnector, RSSConnector
from meemee.context import ContextStore


def test_atom_link_iso_date_and_nested_content_survive_real_http(tmp_path):
    body = b'''<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Release</title>
      <link href="https://example.org/release"/><updated>2026-10-06T10:00:00Z</updated>
      <content type="xhtml"><div xmlns="http://www.w3.org/1999/xhtml">Hello <b>world</b></div></content>
      </entry></feed>'''
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *args): pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connector = RSSConnector("rss", f"http://127.0.0.1:{server.server_port}/feed")
        row = connector.fetch("alice", "feed")[0]
        assert row.external_id == "https://example.org/release"
        assert row.title == "Release" and row.content == "Hello world"
        assert row.occurred_at == "2026-10-06T10:00:00+00:00"
        assert row.metadata["timestamp_known"]
        store = ContextStore(tmp_path / "context.db")
        store.register_source("alice", "feed", "rss", {})
        assert store.ingest(row)
        assert store.assemble("alice", "world")["records"][0]["content"] == "Hello world"
        assert store.assemble("bob", "world")["records"] == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_rss_rejects_entity_expansion_instead_of_loading_local_files(monkeypatch):
    from defusedxml.common import DefusedXmlException
    connector = RSSConnector("rss", "https://example.org/feed")
    monkeypatch.setattr(connector, "read", lambda: b'<!DOCTYPE x [<!ENTITY private SYSTEM "file:///etc/passwd">]><rss>&private;</rss>')
    with pytest.raises(DefusedXmlException):
        connector.fetch("a", "feed")


def test_feed_ceiling_rejects_truncation(monkeypatch):
    class Response:
        status = 200
        def read(self, count): return b"x" * count
        def __enter__(self): return self
        def __exit__(self, *args): pass
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: Response())
    with pytest.raises(ValueError, match="truncation"):
        RSSConnector("rss", "https://example.org").read()


def test_calendar_timezone_folded_text_and_recurrence_overrides(monkeypatch):
    body = b'''BEGIN:VCALENDAR\r
VERSION:2.0\r
BEGIN:VEVENT\r
UID:e1\r
DTSTART;TZID=Asia/Kolkata:20261007T093000\r
DTEND;TZID=Asia/Kolkata:20261007T103000\r
RRULE:FREQ=DAILY;COUNT=3\r
SUMMARY:Long title\r
 continued\r
DESCRIPTION:Line one\\nLine two\\, detail\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:e1\r
RECURRENCE-ID;TZID=Asia/Kolkata:20261008T093000\r
DTSTART;TZID=Asia/Kolkata:20261008T113000\r
SUMMARY:Override\r
END:VEVENT\r
END:VCALENDAR\r
'''
    connector = ICSConnector("ics", "https://example.org/calendar")
    monkeypatch.setattr(connector, "read", lambda: body)
    rows = connector.fetch("a", "calendar")
    assert rows[0].occurred_at == "2026-10-07T04:00:00+00:00"
    assert rows[0].metadata["end"] == "2026-10-07T05:00:00+00:00"
    assert rows[0].title == "Long titlecontinued"
    assert rows[0].content == "Line one\nLine two, detail"
    assert "FREQ=DAILY" in rows[0].metadata["recurrence_rule"]
    assert not rows[0].metadata["recurrence_expanded"]
    assert rows[1].external_id != rows[0].external_id
    assert rows[1].occurred_at == "2026-10-08T06:00:00+00:00"


def test_calendar_floating_time_is_not_silently_assigned_utc(monkeypatch):
    body = b'BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:x\r\nDTSTART:20261007T093000\r\nEND:VEVENT\r\nEND:VCALENDAR'
    connector = ICSConnector("ics", "https://example.org/calendar")
    monkeypatch.setattr(connector, "read", lambda: body)
    with pytest.raises(ValueError, match="floating"):
        connector.fetch("a", "calendar")
    connector.default_timezone = __import__("zoneinfo").ZoneInfo("Asia/Kolkata")
    assert connector.fetch("a", "calendar")[0].occurred_at == "2026-10-07T04:00:00+00:00"


def test_calendar_all_day_remains_date_not_invented_midnight(monkeypatch):
    connector = ICSConnector("ics", "https://example.org/calendar")
    monkeypatch.setattr(connector, "read", lambda: b'BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:x\r\nDTSTART;VALUE=DATE:20261007\r\nEND:VEVENT\r\nEND:VCALENDAR')
    row = connector.fetch("a", "calendar")[0]
    assert row.occurred_at == "2026-10-07" and row.metadata["all_day"]


def _mail(ident, time, payload):
    return {"id": ident, "threadId": "thread", "internalDate": str(time), "payload": payload}


@respx.mock
def test_gmail_full_pagination_nested_mime_and_monotonic_overlap_cursor(tmp_path):
    root = "https://gmail.googleapis.com/gmail/v1/users/me"
    def listing(request):
        assert request.url.params["q"] == "in:inbox after:1699999999"
        if request.url.params.get("pageToken") == "p2":
            return httpx.Response(200, json={"messages": [{"id": "old"}, {"id": "new"}]})
        return httpx.Response(200, json={"messages": [{"id": "new"}], "nextPageToken": "p2"})
    respx.get(root + "/messages").mock(side_effect=listing)
    encoded = base64.urlsafe_b64encode("Nested body café".encode()).decode().rstrip("=")
    nested = {"mimeType": "multipart/mixed", "parts": [{"mimeType": "multipart/alternative", "parts": [
        {"mimeType": "text/plain", "body": {"data": encoded}}]}]}
    respx.get(root + "/messages/new", params={"format": "full"}).mock(
        return_value=httpx.Response(200, json=_mail("new", 1700000000900, nested)))
    respx.get(root + "/messages/old", params={"format": "full"}).mock(
        return_value=httpx.Response(200, json=_mail("old", 1700000000100, {"mimeType": "text/plain", "body": {"attachmentId": "body1"}})))
    respx.get(root + "/messages/old/attachments/body1").mock(return_value=httpx.Response(200, json={"data": encoded}))
    rows = GmailConnector("token").fetch("a", "gmail", cursor="1700000000")
    assert [row.external_id for row in rows] == ["old", "new"]
    assert all(row.content == "Nested body café" and row.metadata["body_complete"] for row in rows)
    store = ContextStore(tmp_path / "c.db")
    store.register_source("a", "gmail", "gmail", {})
    for row in rows: store.ingest(row)
    assert store.cursor("a", "gmail") == "1700000000"
    assert len(store.recent("a")) == 2


@respx.mock
def test_gmail_page_limit_fails_without_returning_partial_records():
    root = "https://gmail.googleapis.com/gmail/v1/users/me"
    respx.get(root + "/messages").mock(return_value=httpx.Response(200, json={"nextPageToken": "p2"}))
    with pytest.raises(RuntimeError, match="no cursor"):
        GmailConnector("token", max_pages=1).fetch("a", "gmail")


@respx.mock
def test_gmail_repeated_page_token_is_a_blocker_not_a_partial_success():
    root = "https://gmail.googleapis.com/gmail/v1/users/me"
    respx.get(root + "/messages").mock(return_value=httpx.Response(200, json={"nextPageToken": "same"}))
    with pytest.raises(RuntimeError, match="repeated"):
        GmailConnector("token").fetch("a", "gmail")


@respx.mock
def test_gmail_oldest_first_ingestion_preserves_newest_second_cursor(tmp_path):
    root = "https://gmail.googleapis.com/gmail/v1/users/me"
    respx.get(root + "/messages").mock(return_value=httpx.Response(200, json={"messages": [{"id": "new"}, {"id": "old"}]}))
    data = base64.urlsafe_b64encode(b"body").decode()
    for ident, millis in [("new", 1700000003000), ("old", 1700000001000)]:
        respx.get(root + f"/messages/{ident}").mock(return_value=httpx.Response(200, json=_mail(ident, millis, {"body": {"data": data}})))
    rows = GmailConnector("token").fetch("a", "gmail")
    store = ContextStore(tmp_path / "c.db")
    store.register_source("a", "gmail", "gmail", {})
    for row in rows: store.ingest(row)
    assert store.cursor("a", "gmail") == "1700000003"


def test_calendar_time_only_change_creates_context_version(tmp_path, monkeypatch):
    connector = ICSConnector("ics", "https://example.org/calendar")
    store = ContextStore(tmp_path / "c.db")
    store.register_source("a", "calendar", "ics", {})
    for start in ["20261007T093000Z", "20261007T103000Z"]:
        body = f"BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:x\r\nDTSTART:{start}\r\nSUMMARY:Unchanged title\r\nEND:VEVENT\r\nEND:VCALENDAR".encode()
        monkeypatch.setattr(connector, "read", lambda body=body: body)
        assert store.ingest(connector.fetch("a", "calendar")[0])
    rows = store.recent("a")
    assert len(rows) == 2
    assert rows[0]["occurred_at"] != rows[1]["occurred_at"]
