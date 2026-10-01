# Guided signup (local-only release)

Opt-in, provider-neutral guided account signup behind explicit per-request approval.
This release supports ONLY the loopback fixture profile (`http://127.0.0.1:PORT`).
There is no arbitrary-website support, no site discovery, and no remote signup.

## Flow
start -> inspect (origin, form contract, exact policy text, reviewed challenge
selectors) -> ready -> user approves the inspection digest -> submit (credentials
filled from an opaque vault reference at the fill boundary only) -> verification
(code or link) -> success only after account readback shows the same email + name.

## Hard stops (state=stopped, nothing submitted or flow halts for the user)
payment, card, paid_trial, subscription, fee, consequential_terms, id, phone,
captcha, bot_restriction, unsupported_auth, unknown_form_controls. These are
explicit reviewed classifications on the profile and selectors, never keyword
guessing. A profile declaring commitments is stopped before any submission.

## Email verification scope
The inbox adapter (GmailInbox) reads the USER'S OWN Gmail via an OAuth token held
in the vault. Every lookup is bound to one request: exact sender, exact recipient,
exact subject, internalDate within [verification_since, deadline], metadata-first
(no bodies of unrelated mail), exactly one Authentication-Results header from mx.google.com, parsed result by result:
a dkim=pass whose header.i is exactly @<approved sender domain> is required and any
other dkim result for that domain rejects (a pass for another domain never counts), exact plain-text templates only, each message id
consumed once (no replay). Links must match the request origin and path/token
shape; wrong domains are rejected without being opened. Message content is never
treated as instructions.

## Safety properties
- Secrets exist only inside the vault database (AES-GCM, same record schema as
  meemee SecretVault). Runs store a whitelisted public field set; tests assert the
  database contains no password, token or code bytes.
- Request gate at the browser routing layer: every non-GET request is aborted unless the
  actor itself opened a phase (submit/verify/resend) after stored approval, it is a
  top-level form navigation, the path matches the phase, and the body equals exactly the
  approved values (email, name, vault password; or the proof code). Page scripts cannot
  open a phase, so a POST during inspection or before approval is aborted. Any request
  whose path or query carries the filled password or a code is aborted, and queries are
  allowed only on the actor-opened verification link.
- Redirects: a 3xx answer (301, 302, 303, 307, 308) to any non-GET/HEAD request is never given
  to the browser. Chrome would replay the approved body on 307/308, so the request is aborted at the
  routing layer and the run becomes `unknown` / `gated_request_redirected` (the server may already
  have processed the original POST; the actor cannot know). A 3xx answer to a GET is passed on only
  if its Location resolves to an allowed same-origin URL (no query except the verification link, no
  fragment, no filled secret), and the followed request goes through the same route guard again.
  Tested for all five statuses against /verify, /resend, /signup and a second origin, fixture only.
- One signup POST per run at the browser routing layer; uncertain outcomes are
  unknown/partial, never retried. Cancel closes local work and honestly reports
  that a remote account may remain; restart is a fresh unapproved request.
- Profile changes invalidate prior approvals (digest mismatch stops the run).
- Process restart marks in-flight runs unknown; they are not silently resumed.
- The HTTP router is explicit host opt-in: the host supplies reviewed profiles,
  vault, inbox and its own authenticated owner dependency; all browser work runs
  on a single dedicated actor thread.

## Not in this release (honest limits)
- Redirect handling covers HTTP 3xx only. A page that navigates itself (meta refresh, script) after an
  approved POST is not a redirect and is covered only by the phase gate, which allows each approved
  POST once and no further state-changing request.
- No real-site profiles are installed; enabling a real site requires a reviewed
  Profile with selectors, exact policy text and sender/subject templates, plus
  production validation beyond this fixture acceptance.
- Real Gmail API behavior is covered by a local metadata-first stub contract in
  tests, not by a live Gmail account in this acceptance run.
- Screenshots are redrawn, not masked. The live page is only asked yes/no questions and
  exact-equality checks (does the policy text equal the reviewed policy, do the account
  email/name equal the stored run values, do the buttons exist). The image is then drawn
  in a separate JS-disabled, network-blocked context from reviewed profile data and the
  stored run, with every string HTML-escaped. No page text, attribute, style,
  pseudo-element or shadow-root content is copied. Password and code fields are also
  cleared in the live page. Tested against: per-character spans, ::before/::after content
  (including attr()), open shadow roots, base64 and reversed text. Consequence: a
  screenshot is a review card, not a picture of the page. Fixture-verified only.
- Network layer (round 3, the real control): Chrome is launched with
  `--host-resolver-rules="MAP * ~NOTFOUND, EXCLUDE 127.0.0.1"`, a refusing proxy
  (`--proxy-server=http://127.0.0.1:1`) and `--proxy-bypass-list=<-loopback>;<reviewed host:port>`,
  QUIC/HTTP3 off, and WebRTC non-proxied UDP off. Workers, SharedWorkers, importScripts, WebSocket,
  preconnect, form target=_blank and CSP reports share this network stack, so they cannot reach any
  other loopback port or hostname. Every fixture response also gets our CSP (worker-src 'none',
  connect-src 'self', form-action 'self') and loses Report-To, Reporting-Endpoints, NEL,
  Report-Only CSP and any site CSP (so no site report-uri survives). A second tab is closed. The init
  script removes Worker, SharedWorker, EventSource, WebRTC, WebTransport and blob URLs (backup only).
  Tests run each vector twice, the second time with the init script, CSP and WebSocket mock all
  switched off ('network_only'), so only the launch flags are under test. Verified with raw TCP and
  UDP canary listeners on other loopback ports. Not verified: WebRTC or WebTransport against a real
  peer (WebTransport needs HTTPS and a QUIC server; the UDP test shows no datagram leaves, not that
  a handshake would fail), a Chrome version other than the installed one, and any channel not listed.
- This is a local-fixture release (loopback only). It has not been run against any real
  website or live Gmail and must not be described as real-site signup.
