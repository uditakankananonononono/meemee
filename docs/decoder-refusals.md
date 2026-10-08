# Decoder refusal repair

Browser takeover WebSocket rejects parsed JSON nesting over64 with an
iterative walk, even when interpreter recursion settings allow deeper JSON.
Existing session remains open after a bad frame so the user can release it.
GitHub mutation response must be an object; unexpected top-level shape returns
an outcome-unknown refusal with no replay.422errors use fixed safe text.

Tests exercise real local WebSocket protocol handling with an injected manager
and HTTP MockTransport, not a live GitHub push/PR or real browser acceptance.
No claim of safe arbitrary response size or full provider schema validation.
