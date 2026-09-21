// Resume-safe SSE client for GET /v1/jobs/{id}/stream.
// The native EventSource cannot send an Authorization header, so this uses
// fetch streaming and parses the event wire format manually. It honours the
// server cursor (id: lines) and reconnects with Last-Event-ID after network
// drops. The server stream terminates on done/failed; for cancel_requested /
// cancelled (which the server keeps streaming heartbeats for) this client
// closes deliberately once a terminal cancel event arrives.
import { apiUrl } from "./api.js";
import { bearerToken } from "./store.js";

const TERMINAL_EVENTS = new Set(["done", "failed", "cancelled"]);

export function openJobStream(jobId, { after = 0, onEvent, onState } = {}) {
  let cursor = after;
  let stopped = false;
  let attempt = 0;
  let controller = null;

  const emit = (state) => { if (onState) onState(state); };

  async function connect() {
    if (stopped) return;
    controller = new AbortController();
    const headers = { accept: "text/event-stream" };
    const token = bearerToken.get();
    if (token) headers.authorization = `Bearer ${token}`;
    if (cursor > 0) headers["last-event-id"] = String(cursor);
    let response;
    try {
      response = await fetch(apiUrl(`/v1/jobs/${encodeURIComponent(jobId)}/stream?after=${cursor}`), {
        headers, credentials: "same-origin", signal: controller.signal,
      });
    } catch (error) {
      if (stopped) return;
      scheduleReconnect(`connection failed: ${error.message}`);
      return;
    }
    if (!response.ok || !response.body) {
      if (response.status === 401 || response.status === 403 || response.status === 404) {
        emit({ state: "error", detail: `stream refused: HTTP ${response.status}` });
        stopped = true;
        return;
      }
      if (!stopped) scheduleReconnect(`stream HTTP ${response.status}`);
      return;
    }
    attempt = 0;
    emit({ state: "open" });
    const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
    let buffer = "";
    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += value;
        let boundary;
        while ((boundary = buffer.indexOf("\n\n")) >= 0) {
          const raw = buffer.slice(0, boundary);
          buffer = buffer.slice(boundary + 2);
          const event = parseEvent(raw);
          if (!event) continue;
          if (event.id) cursor = Number(event.id) || cursor;
          if (onEvent) onEvent(event, cursor);
          if (TERMINAL_EVENTS.has(event.event)) {
            stopped = true;
            emit({ state: "closed", detail: `terminal event: ${event.event}` });
            try { await reader.cancel(); } catch { /* already closed */ }
            return;
          }
        }
      }
    } catch (error) {
      if (stopped) return;
      scheduleReconnect(`read failed: ${error.message}`);
      return;
    }
    // Server closed the stream (it does this after done/failed is drained).
    if (!stopped) {
      stopped = true;
      emit({ state: "closed", detail: "server ended the stream" });
    }
  }

  function scheduleReconnect(reason) {
    attempt += 1;
    const delay = Math.min(15000, 500 * 2 ** attempt);
    emit({ state: "reconnecting", detail: reason, retryInMs: delay, attempt });
    setTimeout(connect, delay);
  }

  connect();
  return {
    get cursor() { return cursor; },
    close() {
      stopped = true;
      emit({ state: "closed", detail: "closed by viewer" });
      if (controller) controller.abort();
    },
  };
}

function parseEvent(raw) {
  let data = "";
  let event = "message";
  let id = null;
  for (const line of raw.split("\n")) {
    if (!line || line.startsWith(":")) continue; // comments / heartbeats
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "data") data = data ? `${data}\n${value}` : value;
    else if (field === "event") event = value;
    else if (field === "id") id = value;
  }
  if (!data) return null;
  let parsed = null;
  try { parsed = JSON.parse(data); } catch { /* keep raw */ }
  return { event, id, data: parsed ?? data };
}
