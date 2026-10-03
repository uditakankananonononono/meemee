"""Exact-template, time/recipient/sender scoped Gmail reader.

No instruction-following, model calls, global OTP search or heuristic extraction.
Real Gmail integration is not acceptance-tested in this local-only release.
"""
import base64
from email.utils import parseaddr
import re

import httpx


MAX_RESPONSE = 262144      # bytes; metadata/list/full JSON from the fixed Gmail host


class _Bounded:
    """Minimal response with the same .raise_for_status()/.json() the inbox uses."""
    def __init__(self, status, raw):
        self.status, self.raw = status, raw

    def raise_for_status(self):
        if self.status >= 400:
            raise httpx.HTTPStatusError('gmail request failed', request=None, response=None)

    def json(self):
        import json
        return json.loads(self.raw)


class GmailInbox:
    def __init__(self, vault, token_ref, client=None, base='https://gmail.googleapis.com/gmail/v1/users/me', local_test=False, token_provider=None):
        if base != 'https://gmail.googleapis.com/gmail/v1/users/me':
            from urllib.parse import urlsplit
            p = urlsplit(base)
            if not local_test or p.scheme != 'http' or p.hostname != '127.0.0.1':
                raise ValueError('Gmail endpoint is fixed outside local testing')
        self.vault, self.token_ref, self.base = vault, token_ref, base
        self.token_provider = token_provider   # e.g. GmailOAuth.access_token; else a vault-held bearer token
        self.client = client or httpx.Client(timeout=5, follow_redirects=False, trust_env=False)
        self.used = set()

    def _get(self, url, headers=None, params=None):
        if not hasattr(self.client, 'stream'):
            return self.client.get(url, headers=headers, params=params)
        with self.client.stream('GET', url, headers=headers, params=params) as response:
            raw = b''
            for chunk in response.iter_bytes():
                raw += chunk
                if len(raw) > MAX_RESPONSE:
                    raise ValueError('gmail response too large')
            return _Bounded(response.status_code, raw)

    def proof(self, scope):
        # Bound all queries to user-owned Gmail; no app mailbox is used.
        token = self.token_provider() if self.token_provider else self.vault.get(self.token_ref)
        headers = {'Authorization': 'Bearer '+token}
        # Exact address/subject checks below backstop Gmail query syntax.
        q = f'from:{scope["sender"]} to:{scope["recipient"]} after:{int(scope["since"])} before:{int(scope["until"])+1}'
        listing = self._get(self.base+'/messages', headers=headers,
                                  params={'q': q, 'maxResults': 10})
        listing.raise_for_status()
        for item in listing.json().get('messages', [])[:10]:
            mid = item['id']
            if mid in self.used:
                continue
            # Read metadata first. Do not retrieve bodies from unrelated messages.
            metadata = self._get(self.base+'/messages/'+mid, headers=headers,
                                       params={'format': 'metadata'})
            metadata.raise_for_status()
            data = metadata.json()
            if not self._match(data, scope):
                continue
            response = self._get(self.base+'/messages/'+mid, headers=headers,
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
        # Gmail's receiver-authentication header, not body claims. Each result is parsed on
        # its own: one dkim=pass whose signing identity is exactly the approved sender
        # domain, and no failing dkim result for that domain.
        domains = tuple(d.lower() for d in scope.get('dkim_domains') or ()) or (scope['sender'].split('@')[-1].lower(),)
        auths = [h['value'] for h in hs if h['name'].lower() == 'authentication-results']
        if len(auths) != 1:
            return False
        return GmailInbox._aligned_dkim(auths[0], domains)

    @staticmethod
    def _aligned_dkim(header, domains):
        if isinstance(domains, str):
            domains = (domains,)
        parts = [x.strip() for x in header.split(';')]
        if not parts or parts[0].lower() != 'mx.google.com':
            return False
        passed = failed = False
        for result in parts[1:]:
            tokens = result.split()
            if not tokens or '=' not in tokens[0]:
                continue
            method, _, verdict = tokens[0].partition('=')
            if method.lower() != 'dkim':
                continue
            ident = [t.split('=', 1)[1] for t in tokens[1:] if t.lower().startswith('header.i=')]
            if len(ident) != 1 or ident[0].lower() not in {'@'+d for d in domains}:
                continue
            if verdict.lower() == 'pass':
                passed = True
            else:
                failed = True
        return passed and not failed
