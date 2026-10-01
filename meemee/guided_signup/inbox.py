"""Exact-template, time/recipient/sender scoped Gmail reader.

No instruction-following, model calls, global OTP search or heuristic extraction.
Real Gmail integration is not acceptance-tested in this local-only release.
"""
import base64
from email.utils import parseaddr
import re

import httpx


class GmailInbox:
    def __init__(self, vault, token_ref, client=None, base='https://gmail.googleapis.com/gmail/v1/users/me', local_test=False):
        if base != 'https://gmail.googleapis.com/gmail/v1/users/me':
            from urllib.parse import urlsplit
            p = urlsplit(base)
            if not local_test or p.scheme != 'http' or p.hostname != '127.0.0.1':
                raise ValueError('Gmail endpoint is fixed outside local testing')
        self.vault, self.token_ref, self.base = vault, token_ref, base
        self.client = client or httpx.Client(timeout=5, follow_redirects=False)
        self.used = set()

    def proof(self, scope):
        # Bound all queries to user-owned Gmail; no app mailbox is used.
        token = self.vault.get(self.token_ref)
        headers = {'Authorization': 'Bearer '+token}
        # Exact address/subject checks below backstop Gmail query syntax.
        q = f'from:{scope["sender"]} to:{scope["recipient"]} after:{int(scope["since"])} before:{int(scope["until"])+1}'
        listing = self.client.get(self.base+'/messages', headers=headers,
                                  params={'q': q, 'maxResults': 10})
        listing.raise_for_status()
        for item in listing.json().get('messages', [])[:10]:
            mid = item['id']
            if mid in self.used:
                continue
            # Read metadata first. Do not retrieve bodies from unrelated messages.
            metadata = self.client.get(self.base+'/messages/'+mid, headers=headers,
                                       params={'format': 'metadata'})
            metadata.raise_for_status()
            data = metadata.json()
            if not self._match(data, scope):
                continue
            response = self.client.get(self.base+'/messages/'+mid, headers=headers,
                                       params={'format': 'full'})
            response.raise_for_status()
            data = response.json()
            if not self._match(data, scope):
                continue
            payload = data['payload']
            # Only an operator-reviewed exact text/plain template, no HTML or attachments.
            if payload.get('mimeType') != 'text/plain' or payload.get('parts'):
                continue
            encoded = payload.get('body', {}).get('data', '')
            if len(encoded) > 2048:
                continue
            text = base64.urlsafe_b64decode(encoded+'='*((-len(encoded)) % 4)).decode().strip()
            match = re.fullmatch(r'Your verification code is ([0-9]{6})\.', text)
            if match:
                self.used.add(mid)
                return 'code', match.group(1)
            match = re.fullmatch(r'Verify your account: (\S+)', text)
            if match:
                self.used.add(mid)
                return 'link', match.group(1)
        return None

    @staticmethod
    def _match(data, scope):
        timestamp = int(data.get('internalDate', 0))/1000
        if not scope['since'] <= timestamp <= scope['until']:
            return False
        hs = data.get('payload', {}).get('headers', [])
        def one(name):
            values = [h['value'] for h in hs if h['name'].lower() == name.lower()]
            return values[0] if len(values) == 1 else ''
        sender = parseaddr(one('From'))[1]
        if sender != scope['sender'] or parseaddr(one('To'))[1] != scope['recipient'] or one('Subject') != scope['subject']:
            return False
        # Gmail's receiver-authentication header, not body claims. Require aligned DKIM.
        domain = scope['sender'].split('@')[-1]
        auth = one('Authentication-Results')
        return bool(re.search(r'^mx\.google\.com;.*\bdkim=pass\b.*\bheader\.i=@'+re.escape(domain)+r'(?:;|\s|$)', auth, re.S))
