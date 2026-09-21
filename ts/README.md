# @meemee/client

Typed, dependency-free TypeScript client for Meemee on Node 18+ and modern web runtimes.

```ts
import { MeemeeClient } from "@meemee/client";
const client = new MeemeeClient("http://127.0.0.1:8787", { auth: "mee_..." });
const created = await client.jobs.create("Summarise today's queue", { idempotencyKey: crypto.randomUUID() });
for await (const event of client.jobs.streamEvents(created.id)) console.log(event.kind);
```

Synchronous runs leave write approval off unless `approveWrites: true` is explicit. Job creation exposes the server quota snapshot, supports `Idempotency-Key`, and retries a POST only when that key makes replay safe. SSE reconnects with both `after` and `Last-Event-ID`, and ends on `done`, `failed`, or `cancelled`. HTTP errors use typed classes; quota/rate 429 errors expose `retryAfter`.

Missing: browser Authorization Code + PKCE login and admin quota mutation are server/operator workflows, not part of the Python SDK surface mirrored here.
