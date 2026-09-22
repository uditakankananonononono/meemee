// Status view: service health, readiness, session capabilities, Prometheus metrics.
import { h, clear, toast, fullTime } from "../dom.js";
import * as api from "../api.js";
import { reportError } from "../app.js";

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
  const metricsBox = h("div", null, "Metrics require the admin scope.");
  const accountBox = h("div", null, "Loading…");

  root.append(
    h("div", { class: "grid" },
      card("Health", healthBox),
      card("Readiness", readyBox)),
    card("Account and plan", accountBox),
    card("Session capabilities",
      h("p", { class: "muted" },
        "Inferred with side-effect-free probes against the live API (invalid payloads fail validation ",
        "before any write, so a validation error proves the scope is present)."),
      capsBox),
    card("Metrics (admin)", metricsBox),
  );

  api.getHealth()
    .then((data) => clear(healthBox).append(
      kv("status", h("span", { class: "badge badge-ok" }, data.status)),
      kv("version", h("code", null, data.version)),
      kv("checked", fullTime(new Date().toISOString()))))
    .catch((error) => { clear(healthBox).append(h("span", { class: "badge badge-bad" }, error.message)); });

  api.getReady()
    .then((data) => clear(readyBox).append(kv("status", h("span", { class: "badge badge-ok" }, data.status))))
    .catch((error) => clear(readyBox).append(
      h("span", { class: "badge badge-bad" }, error instanceof api.ApiError && error.status === 503 ? "not ready" : "error"),
      h("div", { class: "muted" }, error.message)));

  api.getWhoami()
    .then((data) => {
      const limits = data.entitlement.limits;
      const usage = data.entitlement.usage;
      clear(accountBox).append(
        kv("identity", data.name),
        kv("principal", h("code", null, data.id)),
        kv("scopes", data.scopes.join(", ")),
        kv("plan", h("span", { class: "badge badge-ok" }, data.entitlement.plan)),
        kv("daily jobs", `${usage.daily_jobs} / ${limits.daily_jobs}`),
        kv("active webhooks", `${usage.webhooks} / ${limits.webhooks}`),
        kv("persistent approvals", `${usage.persistent_approvals} / ${limits.persistent_approvals}`));
    })
    .catch((error) => clear(accountBox).append(
      h("span", { class: "badge badge-warn" }, error.status === 401 ? "not authenticated" : error.message)));

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
