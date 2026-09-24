# Builders

One line per builder claim. Claim before you build; do not edit files owned by another claim without coordinating. Each builder works on its own branch; the integrator merges after the full suite passes.

| Builder | Branch | Component | Files owned | Status |
| --- | --- | --- | --- | --- |
| PB1 | pb1 | Interactive browser human takeover (live sessions, takeover links, WebSocket live view, drag relay, takeover-link notices, hand-back) | `meemee/browser_sessions.py`, `meemee/browser_api.py`, `meemee/browser_notices.py`, `meemee/tools/browser_session.py`, `tests/test_browser_takeover.py`, `docs/browser-takeover.md`; small hooks in `meemee/api.py`, `meemee/runtime.py`, `meemee/config.py` | shipped on pb1 |
