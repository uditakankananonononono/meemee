// Status view: service health, readiness, session capabilities, live
// rate-limit budget, and the admin-only Prometheus metrics table.
import { h, clear, missingNote, fullTime } from "../dom.js";
import * as api from "../api.js";

function card(title, ...body) {
  return h("section", { class: "card" }, h("h2", null, title), ...body);
}

function kv(key, value) {
  return h("div", { class: "kv" }, h("span", { class: "kv-key" }, key), h("span", { class: "kv-value" }, value));
}

function scopeRow(label, state, note) {
  const tone = state === true ? "ok" : state === false ? "muted" : "warn";
  const text = state === true ? "granted" : state === false ? "not granted" : "unknown";
  return h("tr", null,
    h("td", null, label),
    h("td", null, h("span", { class: `badge badge-${tone}` }, text)),
    h("td", { class: "muted" }, note || ""));
}

function parsePrometheus(text) {
  const rows = [];
  for (const line of text.split("\n")) {
    if (!line || line.startsWith("#")) continue;
    const match = line.match(/^([a-zA-Z_:][a-zA-Z0-9_:]*)(\{[^}]*\})?\s+(.+)$/);
    if (match) rows.push({ metric: match[1], labels: (match[2] || "").slice(1, -1), value: match[3] });
  }
  return rows;
}

export async function renderStatus(root, ctx) {
  const healthBox = h("div", null, "Loading…");
  const readyBox = h("div", null, "Loading…");
  const capsBox = h("div", null, "Loading…");
  const rateBox = h("div", null, "Waiting for the first measured API call…");
  const metricsBox = h("div", null, "Metrics require the admin scope.");

  const auto = h("input", { type: "checkbox" });
  let autoTimer = null;
  auto.addEventListener("change", () => {
    if (auto.checked) autoTimer = setInterval(loadHealth, 30000);
    else if (autoTimer) { clearInterval(autoTimer); autoTimer = null; }
  });
  window.addEventListener("hashchange", () => {
    if (autoTimer) { clearInterval(autoTimer); autoTimer = null; }
  }, { once: true });

  const loadHealth = () => {
    api.getHealth()
      .then((data) => clear(healthBox).append(
        kv("status", h("span", { class: "badge badge-ok" }, data.status)),
        kv("version", h("code", null, data.version)),
        kv("checked", fullTime(new Date().toISOString()))))
      .catch((error) => { clear(healthBox).append(h("span", { class: "badge badge-bad" }, error.message)); });
    api.readiness()
      .then(({ ok, body }) => {
        clear(readyBox);
        if (!body || !body.components) {
          // Pre-v0.19 servers answer {"status":"ready"} with no component detail.
          readyBox.append(kv("status", h("span", { class: `badge badge-${ok ? "ok" : "bad"}` }, (body && body.status) || (ok ? "ready" : "not ready"))));
          return;
        }
        readyBox.append(kv("status", h("span", { class: `badge badge-${ok ? "ok" : "bad"}` }, body.status)));
        const rows = Object.entries(body.components).map(([name, component]) => {
          const detail = component.error
            ? component.error
            : name === "disk"
              ? `${(component.free_bytes / 1e9).toFixed(1)} GB free (minimum ${(component.minimum_bytes / 1e9).toFixed(1)} GB)`
              : name === "model" && component.status !== undefined
                ? `HTTP ${component.status}`
                : "";
          return h("tr", null,
            h("td", null, name),
            h("td", null, h("span", { class: `badge badge-${component.ok ? "ok" : "bad"}` }, component.ok ? "ok" : "failing")),
            h("td", { class: "muted" }, detail));
        });
        readyBox.append(h("table", { class: "table" },
          h("thead", null, h("tr", null, h("th", null, "Component"), h("th", null, "State"), h("th", null, "Detail"))),
          h("tbody", null, rows)));
      })
      .catch((error) => clear(readyBox).append(
        h("span", { class: "badge badge-bad" }, "error"),
        h("div", { class: "muted" }, error.message)));
  };

  root.append(
    h("div", { class: "grid" },
      card("Health", healthBox),
      card("Readiness", readyBox)),
    card("Session capabilities",
      h("p", { class: "muted" },
        "Inferred with side-effect-free probes against the live API (invalid payloads fail validation ",
        "before any write, so a validation error proves the scope is present)."),
      capsBox),
    card("Rate-limit budget",
      h("p", { class: "muted" },
        "Live from the RateLimit-* response headers the API attaches to every measured call ",
        "(health and readiness are exempt by design)."),
      rateBox),
    card("Metrics (admin)", metricsBox),
    h("div", { class: "row" },
      h("label", { class: "check" }, auto, " refresh health/readiness every 30s")),
    missingNote("No /v1/whoami endpoint", "The API cannot return the current principal's name or scopes, so capabilities above are probed rather than read."),
  );

  loadHealth();

  // Live rate-limit budget: refreshed by every API call, re-rendered each
  // second so the reset countdown moves.
  const renderRate = (current) => {
    clear(rateBox);
    if (!current) { rateBox.append("Waiting for the first measured API call…"); return; }
    const resetIn = Math.max(0, current.reset - Math.floor(Date.now() / 1000));
    const tone = current.remaining === 0 ? "bad" : current.remaining <= current.limit * 0.2 ? "warn" : "ok";
    rateBox.append(
      kv("remaining", h("span", { class: `badge badge-${tone}` }, `${current.remaining} / ${current.limit}`)),
      kv("window resets", `${resetIn}s (at ${new Date(current.reset * 1000).toLocaleTimeString()})`),
      kv("last measured", fullTime(new Date(current.observedAt).toISOString())));
  };
  const unsubscribe = api.rateLimit.onChange(renderRate);
  const tick = setInterval(() => renderRate(api.rateLimit.current), 1000);
  window.addEventListener("hashchange", () => { unsubscribe(); clearInterval(tick); }, { once: true });

  const renderCaps = () => {
    clear(capsBox);
    const caps = ctx.caps;
    if (ctx.capsError) {
      capsBox.append(h("p", { class: "bad-text" }, `Could not reach the API: ${ctx.capsError.message}`));
      return;
    }
    if (!caps) { capsBox.append("Probing…"); return; }
    if (!caps.authenticated) {
      capsBox.append(h("p", null,
        "Not authenticated. Use the Session button above to sign in with OIDC or set a bearer token."));
      return;
    }
    capsBox.append(h("table", { class: "table" },
      h("thead", null, h("tr", null, h("th", null, "Scope"), h("th", null, "State"), h("th", null, "Used by"))),
      h("tbody", null,
        scopeRow("admin", caps.admin, "token administration, audit chain, metrics"),
        scopeRow("runs:write", caps.runsWrite, "creating synchronous agent runs"),
        scopeRow("jobs:read", caps.jobsRead, "job status, event log, SSE progress stream"),
        scopeRow("jobs:write", caps.jobsWrite, "creating and cancelling queued jobs"))));
  };
  renderCaps();
  if (!ctx.caps && !ctx.capsError) ctx.refreshCaps().then(renderCaps);

  api.getMetrics()
    .then((text) => {
      const rows = parsePrometheus(text);
      clear(metricsBox).append(
        h("p", { class: "muted" }, `${rows.length} samples from /metrics, read ${fullTime(new Date().toISOString())}.`),
        h("div", { class: "scroll-x" }, h("table", { class: "table" },
          h("thead", null, h("tr", null, h("th", null, "Metric"), h("th", null, "Labels"), h("th", null, "Value"))),
          h("tbody", null, rows.map((row) => h("tr", null,
            h("td", null, h("code", null, row.metric)),
            h("td", { class: "muted" }, row.labels),
            h("td", null, row.value)))))));
    })
    .catch((error) => {
      if (error instanceof api.ApiError && (error.status === 401 || error.status === 403)) {
        clear(metricsBox).append(h("p", { class: "muted" }, "The current credential lacks the admin scope, so /metrics is not available."));
      } else {
        clear(metricsBox).append(h("p", { class: "bad-text" }, error.message));
      }
    });
}
