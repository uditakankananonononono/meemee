# Browser human takeover

Some sites stop an agent with a captcha, a "verify you are human" page or a sign-in code. Meemee keeps the real page open and hands it to a person, then takes it back.

## Flow

1. The agent opens a live session with `browser.session_open` (or an operator calls `POST /v1/browser/sessions`).
2. If the page shows a challenge, Meemee creates a takeover automatically. The agent can also ask for one with `browser.session_request_human` (for a login, an MFA step or a judgment call), and an operator can with `POST /v1/browser/sessions/{id}/takeover`.
3. The takeover returns a link like `https://your-host/browser/takeover#bt_<id>.<token>`. Send it to the person through whatever channel you use. The token sits in the URL fragment, so it is never sent to the server in a request line or logged; the page strips it from the address bar and sends it once over the WebSocket.
4. The person sees the live page (JPEG frames about 3 per second) and can click, double-click, type, press allowed keys, scroll and navigate within the session's domain policy.
5. They press **Done - hand back** (or **Can't do it**). The agent's `browser.session_wait_human` returns the outcome (`completed`, `declined`, `expired`, `session_closed`) and the current page state, and the agent continues on the same page with the same cookies.

While a person holds the session, agent actions are refused.

## Safety

- Takeover tokens are 256-bit random values stored only as SHA-256 digests and compared in constant time. Each link works only for its own takeover, ends when released, and expires after `MEEMEE_BROWSER_TAKEOVER_TTL_SECONDS` (default 900). Input from the person extends the expiry by up to 2 minutes so an active person is not cut off.
- The agent's tool results never contain the raw token; the link does.
- Typed text is never stored. The event log records the character count only. Screenshots are never stored.
- Keys are limited to printable characters, navigation/editing keys and modifier combinations. Clicks must fall inside the viewport.
- The session's `allowed_domains` policy is enforced for agent actions, human navigation and every page navigation (blocked navigations are logged). Private and reserved addresses are refused unless `MEEMEE_BROWSER_ALLOW_PRIVATE_HOSTS=true`.
- At most `MEEMEE_BROWSER_MAX_SESSIONS` (default 4) live sessions; idle agent sessions close after `MEEMEE_BROWSER_IDLE_TIMEOUT_SECONDS` (default 900).

## API

| Method | Path | Scope |
| --- | --- | --- |
| POST | `/v1/browser/sessions` | runs:write |
| GET | `/v1/browser/sessions` | jobs:read (own sessions; admin sees all) |
| GET | `/v1/browser/sessions/{id}` | jobs:read (record, takeovers, event log) |
| POST | `/v1/browser/sessions/{id}/takeover` | runs:write (returns the token once) |
| DELETE | `/v1/browser/sessions/{id}` | runs:write |
| POST | `/v1/browser/takeover/release` | takeover token |
| WS | `/v1/browser/takeover/ws` | takeover token in the first message |
| GET | `/browser/takeover` | viewer page |

WebSocket messages from the viewer: `{"takeover_id","token"}` first, then `{"type":"click","x","y","double"?}`, `{"type":"type","text"}`, `{"type":"key","key":"Control+a"}`, `{"type":"scroll","dx","dy"}`, `{"type":"goto","url"}`, `{"type":"release","outcome":"completed|declined","note"?}`. Server messages: `claimed`, `frame` (`jpeg` base64 + `url`), `ack`, `error`, `released`.

## Limits

- Live pages live in the API server process. The CLI (`meemee run`) and separate worker processes do not register the session tools, because the person's viewer must reach the process that holds the page. After a restart, open sessions are marked `lost`.
- The viewer streams screenshots, not video. It is fine for challenges and sign-ins, not for animation-heavy pages.
- Drag gestures (slider captchas) are not relayed yet.
