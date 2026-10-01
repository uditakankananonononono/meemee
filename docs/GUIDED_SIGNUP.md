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
- One signup POST per run at the browser routing layer; uncertain outcomes are
  unknown/partial, never retried. Cancel closes local work and honestly reports
  that a remote account may remain; restart is a fresh unapproved request.
- Profile changes invalidate prior approvals (digest mismatch stops the run).
- Process restart marks in-flight runs unknown; they are not silently resumed.
- The HTTP router is explicit host opt-in: the host supplies reviewed profiles,
  vault, inbox and its own authenticated owner dependency; all browser work runs
  on a single dedicated actor thread.

## Not in this release (honest limits)
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
- Non-route channels: WebSockets are intercepted with route_web_socket by a handler that
  never connects to a server (verified: a raw listener on another loopback port gets no
  connection). RTCPeerConnection, WebTransport, EventSource and sendBeacon are removed or
  neutralised in every frame by an init script (backup layer; EventSource and beacon are
  also stopped by request routing). Not verified: WebRTC against a real peer, WebTransport
  (needs HTTPS), and any channel not listed here.
- This is a local-fixture release (loopback only). It has not been run against any real
  website or live Gmail and must not be described as real-site signup.
