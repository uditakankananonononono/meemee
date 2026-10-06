from __future__ import annotations

import email.utils
import hashlib
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol
from zoneinfo import ZoneInfo

from defusedxml import ElementTree as ET

from .context import ContextRecord


class Connector(Protocol):
    name: str
    def fetch(self, owner_id: str, source_id: str, cursor: str | None = None) -> list[ContextRecord]: ...


@dataclass
class HTTPFeedConnector:
    name: str
    url: str
    timeout: float = 15

    def read(self) -> bytes:
        request=urllib.request.Request(self.url,headers={"User-Agent":"Meemee/1.0"})
        with urllib.request.urlopen(request,timeout=self.timeout) as response:
            if int(response.status)>=400:raise RuntimeError(f"feed returned HTTP {response.status}")
            body = response.read(2_000_001)
            if len(body) > 2_000_000:
                raise ValueError("feed exceeds 2 MB; refusing silent truncation")
            return body


class RSSConnector(HTTPFeedConnector):
    name="rss"
    def fetch(self, owner_id: str, source_id: str, cursor: str | None = None) -> list[ContextRecord]:
        root = ET.fromstring(self.read())
        items = root.findall(".//item") or root.findall(".//{*}entry")
        records = []
        for item in items:
            def value(*names, item=item):
                for name in names:
                    node = item.find(name)
                    if node is None:
                        node = item.find("{*}" + name)
                    if node is not None:
                        if name == "link" and node.get("href"):
                            return node.get("href")
                        text = "".join(node.itertext()).strip()
                        if text:
                            return text
                return ""
            title = value("title")
            content = value("description", "summary", "content")
            external = value("guid", "id", "link") or hashlib.sha256((title + content).encode()).hexdigest()
            published = value("pubDate", "published", "updated")
            try:
                stamp = email.utils.parsedate_to_datetime(published)
            except (TypeError, ValueError):
                try:
                    stamp = datetime.fromisoformat(published.replace("Z", "+00:00"))
                except ValueError:
                    stamp = None
            metadata = {"published_raw": published, "timestamp_known": stamp is not None}
            if stamp is not None and stamp.tzinfo is None:
                raise ValueError("feed timestamp has no timezone")
            occurred = (stamp or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
            records.append(ContextRecord(owner_id, source_id, external, "document", title, content,
                                         occurred, {"connector": "rss", "url": self.url},
                                         cursor=external, metadata=metadata))
        return records


class ICSConnector(HTTPFeedConnector):
    """Parse RFC5545 via icalendar, preserving recurrence instead of fabricating instances.

    Floating timestamps require an explicit caller-selected timezone. Recurrence rules
    are preserved in metadata; this connector does not expand a recurring schedule.
    """
    name = "ics"

    def __init__(self, name: str, url: str, timeout: float = 15, default_timezone: str | None = None):
        super().__init__(name, url, timeout)
        self.default_timezone = ZoneInfo(default_timezone) if default_timezone else None

    def fetch(self, owner_id: str, source_id: str, cursor: str | None = None) -> list[ContextRecord]:
        from datetime import date

        from icalendar import Calendar
        calendar = Calendar.from_ical(self.read())
        records = []
        for event in calendar.walk("VEVENT"):
            if event.errors:
                raise ValueError(f"invalid calendar properties: {event.errors}")
            if "DTSTART" not in event or "UID" not in event:
                raise ValueError("calendar event requires UID and DTSTART")
            start = event.decoded("DTSTART")
            def stamp(value):
                if isinstance(value, datetime):
                    if value.tzinfo is None:
                        if self.default_timezone is None:
                            raise ValueError("floating calendar time requires default_timezone")
                        value = value.replace(tzinfo=self.default_timezone)
                    return value.astimezone(timezone.utc).isoformat()
                if isinstance(value, date):
                    return value.isoformat()
                raise ValueError("invalid calendar date/time")
            occurred = stamp(start)
            uid = str(event["UID"])
            recurrence_id = stamp(event.decoded("RECURRENCE-ID")) if "RECURRENCE-ID" in event else None
            external = uid if recurrence_id is None else f"{uid}:{recurrence_id}"
            metadata = {
                "all_day": not isinstance(start, datetime),
                "end": stamp(event.decoded("DTEND")) if "DTEND" in event else None,
                "recurrence_id": recurrence_id,
                "recurrence_rule": event["RRULE"].to_ical().decode() if "RRULE" in event else None,
                "recurrence_expanded": False,
                "status": str(event.get("STATUS", "CONFIRMED")),
            }
            records.append(ContextRecord(owner_id, source_id, external, "event",
                                         str(event.get("SUMMARY", "Calendar event")),
                                         str(event.get("DESCRIPTION", "")), occurred,
                                         {"connector": "ics", "url": self.url,
                                          "location": str(event.get("LOCATION", ""))},
                                         cursor=external, metadata=metadata))
        return records


class SignedWebhookConnector:
    name="signed_webhook"
    @staticmethod
    def parse(owner_id: str, source_id: str, payload: dict) -> ContextRecord:
        required=("id","kind","title","content","occurred_at")
        if any(not payload.get(key) for key in required):raise ValueError("webhook event requires id, kind, title, content and occurred_at")
        return ContextRecord(owner_id,source_id,str(payload["id"]),payload["kind"],str(payload["title"]),str(payload["content"]),str(payload["occurred_at"]),{"connector":"signed_webhook","received":True},payload.get("visibility","private"),payload.get("cursor"),payload.get("metadata",{}))

@dataclass
class GmailConnector:
    """Read Gmail messages through the OAuth bearer supplied by the owner."""
    access_token: str
    address: str = "me"
    base_url: str = "https://gmail.googleapis.com/gmail/v1"
    timeout: float = 15
    name: str = "gmail"
    max_pages: int = 100

    @property
    def connected(self) -> bool:
        return bool(self.access_token)

    def _get(self, path: str, params: dict | None = None) -> dict:
        import httpx
        if not self.connected:
            raise RuntimeError("Gmail is not connected; complete OAuth with gmail.readonly permission")
        response = httpx.get(f"{self.base_url}/users/{self.address}/{path}", params=params,
                             headers={"Authorization": f"Bearer {self.access_token}"}, timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def fetch(self, owner_id: str, source_id: str, cursor: str | None = None) -> list[ContextRecord]:
        import base64
        from email.message import Message
        # Overlap the cursor second: Gmail after: is exclusive. ContextStore dedupes
        # immutable versions, so same-second arrivals must not be silently lost.
        params = {"maxResults": 100, "q": "in:inbox"}
        if cursor:
            if not cursor.isdigit():
                raise ValueError("Gmail cursor must be epoch seconds")
            params["q"] += f" after:{max(0, int(cursor) - 1)}"
        if self.max_pages < 1:
            raise ValueError("max_pages must be positive")
        records, seen, pages = [], set(), set()
        def decode_parts(part):
            mime = part.get("mimeType", "")
            if mime == "text/plain" or (not mime and part.get("body", {}).get("data")):
                body = part.get("body", {})
                data = body.get("data")
                if not data and body.get("attachmentId"):
                    data = self._get(f"messages/{message['id']}/attachments/{body['attachmentId']}").get("data")
                if data:
                    content_type = next((h["value"] for h in part.get("headers", [])
                                         if h["name"].lower() == "content-type"), "text/plain; charset=utf-8")
                    header = Message()
                    header["content-type"] = content_type
                    charset = header.get_content_charset() or "utf-8"
                    raw = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
                    return [raw.decode(charset, "replace")]
            result = []
            for child in part.get("parts", []):
                result.extend(decode_parts(child))
            return result
        for _ in range(self.max_pages):
            listing = self._get("messages", params)
            for item in listing.get("messages", []):
                if item["id"] in seen:
                    continue
                seen.add(item["id"])
                message = self._get(f"messages/{item['id']}", {"format": "full"})
                payload = message.get("payload", {})
                headers = {row["name"].lower(): row["value"] for row in payload.get("headers", [])}
                parts = decode_parts(payload)
                body = "\n".join(parts) if parts else message.get("snippet", "")
                millis = int(message["internalDate"])
                stamp = datetime.fromtimestamp(millis / 1000, timezone.utc).isoformat()
                records.append(ContextRecord(owner_id, source_id, item["id"], "document",
                    headers.get("subject", "Email"), body, stamp,
                    {"connector": "gmail", "message_id": item["id"], "thread_id": message.get("threadId"),
                     "from": headers.get("from")}, cursor=str(millis // 1000),
                    metadata={"to": headers.get("to"), "body_complete": bool(parts)}))
            token = listing.get("nextPageToken")
            if not token:
                return sorted(records, key=lambda record: (record.occurred_at, record.external_id))
            if token in pages:
                raise RuntimeError("Gmail repeated a page token; refusing partial sync")
            pages.add(token)
            params["pageToken"] = token
        raise RuntimeError(f"Gmail sync exceeded {self.max_pages} pages; no cursor may be advanced")
