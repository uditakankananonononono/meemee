from __future__ import annotations

import email.utils
import hashlib
import json
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

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
            return response.read(2_000_001)[:2_000_000]


class RSSConnector(HTTPFeedConnector):
    name="rss"
    def fetch(self, owner_id: str, source_id: str, cursor: str | None = None) -> list[ContextRecord]:
        root=ET.fromstring(self.read());items=root.findall('.//item') or root.findall('.//{*}entry');records=[]
        for item in items:
            def value(*names, item=item):
                for name in names:
                    node=item.find(name) or item.find('{*}'+name)
                    if node is not None and node.text:return node.text.strip()
                return ''
            title=value('title');content=value('description','summary','content');external=value('guid','id','link') or hashlib.sha256((title+content).encode()).hexdigest();published=value('pubDate','published','updated')
            try:occurred=email.utils.parsedate_to_datetime(published).astimezone(timezone.utc).isoformat()
            except (TypeError,ValueError):occurred=datetime.now(timezone.utc).isoformat()
            records.append(ContextRecord(owner_id,source_id,external,'document',title,content,occurred,{"connector":"rss","url":self.url},cursor=external))
        return records


class ICSConnector(HTTPFeedConnector):
    name="ics"
    def fetch(self, owner_id: str, source_id: str, cursor: str | None = None) -> list[ContextRecord]:
        lines=self.read().decode('utf-8','replace').replace('\r\n ','').splitlines();records=[];current=None
        for line in lines:
            if line=='BEGIN:VEVENT':current={}
            elif line=='END:VEVENT' and current is not None:
                uid=current.get('UID') or hashlib.sha256(json.dumps(current,sort_keys=True).encode()).hexdigest();start=current.get('DTSTART','')
                occurred=_ics_time(start);records.append(ContextRecord(owner_id,source_id,uid,'event',current.get('SUMMARY','Calendar event'),current.get('DESCRIPTION',''),occurred,{"connector":"ics","url":self.url,"location":current.get('LOCATION')},cursor=uid,metadata={"end":current.get('DTEND')}));current=None
            elif current is not None and ':' in line:
                key,value=line.split(':',1);current[key.split(';',1)[0]]=value.replace('\\n','\n')
        return records


def _ics_time(value: str) -> str:
    for pattern in ('%Y%m%dT%H%M%SZ','%Y%m%dT%H%M%S','%Y%m%d'):
        try:return datetime.strptime(value,pattern).replace(tzinfo=timezone.utc).isoformat()
        except ValueError:pass
    return datetime.now(timezone.utc).isoformat()


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
        params = {"maxResults": 100, "q": "in:inbox"}
        if cursor:
            params["q"] += f" after:{cursor}"
        listing = self._get("messages", params)
        records = []
        for item in listing.get("messages", []):
            message = self._get(f"messages/{item['id']}", {"format": "full"})
            headers = {row["name"].lower(): row["value"] for row in message.get("payload", {}).get("headers", [])}
            data = message.get("payload", {}).get("body", {}).get("data", "")
            if not data:
                for part in message.get("payload", {}).get("parts", []):
                    if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
                        data = part["body"]["data"]
                        break
            body = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace") if data else message.get("snippet", "")
            stamp = datetime.fromtimestamp(int(message.get("internalDate", "0")) / 1000, timezone.utc).isoformat()
            records.append(ContextRecord(owner_id, source_id, item["id"], "document", headers.get("subject", "Email"), body, stamp,
                                         {"connector": "gmail", "message_id": item["id"], "thread_id": message.get("threadId"), "from": headers.get("from")},
                                         cursor=str(int(message.get("internalDate", "0")) // 1000), metadata={"to": headers.get("to")}))
        return records
