// Jobs view: create, track, watch (SSE) and cancel durable queued jobs.
// The API has no job listing endpoint, so the console follows jobs created
// here or added by ID, refreshing each known job against the source of truth.
import { h, clear, toast, missingNote, statusBadge, jsonBlock, fullTime, timeAgo } from "../dom.js";
import * as api from "../api.js";
import { openJobStream } from "../sse.js";
import { knownJobs } from "../store.js";
import { reportError } from "../app.js";

const TERMINAL = new Set(["done", "failed", "cancelled"]);
let pollTimer = null;
let activeStream = null;

function parseResult(job) {
  if (!job || !job.result) return null;
  try { return JSON.parse(job.result); } catch { return job.result; }
}

function eventLine(event) {
  const when = event.created_at ? fullTime(event.created_at) : "";
  const payload = event.payload && Object.keys(event.payload).length
    ? JSON.stringify(event.payload)
    : "";
  return h("tr", null,
    h("td", { class: "muted nowrap" }, `#${event.sequence}`),
    h("td", null, statusBadge(event.kind)),
    h("td", { class: "muted nowrap" }, when),
    h("td", { class: "payload" }, payload));
}

function jobDetail(root, jobId, ctx) {
  const box = h("section", { class: "card" }, h("h2", null, "Job detail"), h("p", { class: "muted" }, "Loading…"));
  let streamState = h("span", { class: "badge badge-muted" }, "stream off");
  let eventsTable = null;
  const seen = new Set();

  const render = (job) => {
    clear(box);
    const result = parseResult(job);
    const eventsHeader = h("div", { class: "row spread" },
      h("h3", null, "Events"),
      h("span", null, streamState));
    eventsTable = h("table", { class: "table" },
      h("thead", null, h("tr", null,
        h("th", null, "Seq"), h("th", null, "Kind"), h("th", null, "At"), h("th", null, "Payload"))),
      h("tbody", null));
    const cancelButton = h("button", { class: "button button-danger", type: "button" }, "Cancel job");
    if (TERMINAL.has(job.status)) cancelButton.disabled = true;
    cancelButton.addEventListener("click", async () => {
      if (!window.confirm(`Cancel job ${jobId}?`)) return;
      try {
        const outcome = await api.cancelJob(jobId);
        toast(`Job ${outcome.status}.`, "ok");
        refreshDetail();
      } catch (error) {
        if (error instanceof api.ApiError && error.status === 409) {
          toast(`Cannot cancel: ${error.detail}`, "warn");
        } else reportError(error, "cancel failed");
        refreshDetail();
      }
    });
    box.append(
      h("div", { class: "row spread" },
        h("h2", null, "Job detail"),
        h("div", { class: "row" },
          h("button", { class: "button button-quiet", type: "button", onclick: () => refreshDetail() }, "Refresh"),
          cancelButton)),
      h("div", { class: "kv-grid" },
        h("div", { class: "kv" }, h("span", { class: "kv-key" }, "id"), h("code", null, job.id)),
        h("div", { class: "kv" }, h("span", { class: "kv-key" }, "status"), statusBadge(job.status)),
        h("div", { class: "kv" }, h("span", { class: "kv-key" }, "attempts"), `${job.attempts} / ${job.max_attempts}`),
        h("div", { class: "kv" }, h("span", { class: "kv-key" }, "run at"), fullTime(job.run_at)),
        h("div", { class: "kv" }, h("span", { class: "kv-key" }, "created"), fullTime(job.created_at)),
        h("div", { class: "kv" }, h("span", { class: "kv-key" }, "updated"), `${fullTime(job.updated_at)} (${timeAgo(job.updated_at)})`)),
      h("h3", null, "Goal"), h("p", null, job.goal),
      job.error ? h("div", null, h("h3", null, "Error"), h("p", { class: "bad-text" }, job.error)) : null,
      result !== null ? h("div", null, h("h3", null, "Result"), jsonBlock(result)) : null,
      eventsHeader, h("div", { class: "scroll-x" }, eventsTable));
    loadEvents(0, true);
  };

  const appendEvent = (event) => {
    if (!eventsTable || seen.has(event.sequence)) return;
    seen.add(event.sequence);
    eventsTable.querySelector("tbody").append(eventLine(event));
  };

  const loadEvents = async (after, fresh) => {
    try {
      const data = await api.getJobEvents(jobId, after);
      if (fresh) seen.clear(), eventsTable && clear(eventsTable.querySelector("tbody"));
      for (const event of data.events) appendEvent(event);
      return data.events.length ? data.events[data.events.length - 1].sequence : after;
    } catch (error) {
      reportError(error, "could not load job events");
      return after;
    }
  };

  const refreshDetail = async () => {
    try {
      const job = await api.getJob(jobId);
      render(job);
      startStream(job);
    } catch (error) {
      if (error instanceof api.ApiError && error.status === 404) {
        clear(box).append(h("h2", null, "Job detail"),
          h("p", { class: "bad-text" }, "Job not found on the server (deleted or wrong ID)."),
          h("button", { class: "button button-quiet", type: "button", onclick: () => { knownJobs.forget(jobId); box.remove(); } },
            "Forget this job"));
      } else reportError(error, "could not load job");
    }
  };

  const startStream = async (job) => {
    if (activeStream) { activeStream.close(); activeStream = null; }
    if (TERMINAL.has(job.status)) {
      streamState.textContent = `stream closed (${job.status})`;
      return;
    }
    streamState.textContent = "connecting…";
    streamState.className = "badge badge-info";
    activeStream = openJobStream(jobId, {
      onEvent: (event) => {
        const parsed = event.data && typeof event.data === "object" ? event.data : null;
        if (parsed && parsed.sequence !== undefined) appendEvent(parsed);
      },
      onState: async (state) => {
        if (state.state === "open") { streamState.textContent = "live"; streamState.className = "badge badge-ok"; }
        else if (state.state === "reconnecting") {
          streamState.textContent = `reconnecting (${Math.round(state.retryInMs / 1000)}s)`;
          streamState.className = "badge badge-warn";
        } else if (state.state === "closed") {
          streamState.textContent = `closed - ${state.detail || ""}`;
          streamState.className = "badge badge-muted";
          try {
            const fresh = await api.getJob(jobId);
            if (fresh && TERMINAL.has(fresh.status)) render(fresh);
          } catch { /* detail refresh is best-effort here */ }
        } else if (state.state === "error") {
          streamState.textContent = state.detail || "stream error";
          streamState.className = "badge badge-bad";
        }
      },
    });
  };

  refreshDetail();
  return box;
}

export async function renderJobs(root, ctx) {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  if (activeStream) { activeStream.close(); activeStream = null; }

  const goal = h("textarea", { class: "input", rows: 3, maxlength: 20000, placeholder: "Goal for the queued job…" });
  const runAt = h("input", { class: "input", type: "datetime-local" });
  const listBox = h("div");
  const detailHost = h("div");

  const renderList = async () => {
    const entries = knownJobs.all();
    clear(listBox);
    if (!entries.length) {
      listBox.append(h("p", { class: "muted" }, "No jobs tracked in this browser yet. Create one above or add one by ID."));
      return;
    }
    const rows = await Promise.all(entries.map(async (entry) => {
      try {
        const job = await api.getJob(entry.id);
        return { entry, job };
      } catch (error) {
        return { entry, job: null, error };
      }
    }));
    const table = h("table", { class: "table" },
      h("thead", null, h("tr", null,
        h("th", null, "Status"), h("th", null, "Goal"), h("th", null, "Attempts"),
        h("th", null, "Run at"), h("th", null, "Updated"), h("th", null, ""))),
      h("tbody", null));
    for (const { entry, job, error } of rows) {
      if (!job) {
        table.querySelector("tbody").append(h("tr", null,
          h("td", null, h("span", { class: "badge badge-bad" }, error instanceof api.ApiError && error.status === 404 ? "not found" : "error")),
          h("td", { class: "muted" }, h("code", null, entry.id)),
          h("td", { colspan: 3, class: "muted" }, error ? error.message : "unreachable"),
          h("td", null, h("button", { class: "button button-quiet", type: "button", onclick: () => { knownJobs.forget(entry.id); renderList(); } }, "Forget"))));
        continue;
      }
      const open = h("button", { class: "button button-quiet", type: "button" }, "Open");
      open.addEventListener("click", () => {
        clear(detailHost).append(jobDetail(detailHost, job.id, ctx));
        detailHost.scrollIntoView({ behavior: "smooth", block: "start" });
      });
      table.querySelector("tbody").append(h("tr", { class: TERMINAL.has(job.status) ? "" : "row-live" },
        h("td", null, statusBadge(job.status)),
        h("td", null, job.goal.slice(0, 80)),
        h("td", null, `${job.attempts}/${job.max_attempts}`),
        h("td", { class: "muted nowrap" }, fullTime(job.run_at)),
        h("td", { class: "muted nowrap" }, timeAgo(job.updated_at)),
        h("td", null, open)));
    }
    listBox.append(table);
  };

  const create = h("button", { class: "button", type: "button" }, "Queue job");
  // One idempotency key per form fill. A retried submission (double click,
  // network retry) replays the same key, so the server can never enqueue a
  // duplicate of this exact request; the key rotates after a successful queue.
  let idemKey = crypto.randomUUID();
  const idemLabel = h("span", { class: "muted small" });
  const renderIdem = () => { idemLabel.textContent = `idempotency key: ${idemKey} (replays return the original job)`; };
  renderIdem();
  create.addEventListener("click", async () => {
    const text = goal.value.trim();
    if (text.length < 2) { toast("Goal must be at least 2 characters.", "warn"); return; }
    let iso = null;
    if (runAt.value) {
      const when = new Date(runAt.value);
      if (Number.isNaN(when.getTime())) { toast("run_at must be a valid date/time.", "warn"); return; }
      iso = when.toISOString();
    }
    create.disabled = true;
    try {
      const created = await api.createJob(text, iso, { idempotencyKey: idemKey });
      knownJobs.remember(created.id, { goal: text.slice(0, 80) });
      const quota = created.quota
        ? ` Daily quota: ${created.quota.used}/${created.quota.limit} used (${created.quota.remaining} left, UTC day ${created.quota.day}).`
        : "";
      toast(`Job queued: ${created.id}.${quota}`, "ok", 8000);
      goal.value = ""; runAt.value = "";
      idemKey = crypto.randomUUID();
      renderIdem();
      refreshQuota();
      await renderList();
      clear(detailHost).append(jobDetail(detailHost, created.id, ctx));
    } catch (error) {
      if (error instanceof api.ApiError && error.status === 403) {
        toast("The current credential lacks jobs:write.", "bad");
      } else if (error instanceof api.ApiError && error.status === 409) {
        toast(`Idempotency conflict: ${error.detail}. The key was already used with a different request; a fresh key was generated.`, "bad", 9000);
        idemKey = crypto.randomUUID();
        renderIdem();
      } else if (error instanceof api.ApiError && error.status === 429 && /quota/i.test(error.detail)) {
        toast(`Daily job quota exceeded: ${error.detail}. Resets at midnight UTC.`, "warn", 9000);
        refreshQuota();
      } else reportError(error, "could not create job");
    } finally { create.disabled = false; }
  });

  const addId = h("input", { class: "input", placeholder: "Existing job ID (hex)", style: "max-width:22rem" });
  const addButton = h("button", { class: "button button-quiet", type: "button" }, "Track job");
  addButton.addEventListener("click", async () => {
    const id = addId.value.trim();
    if (!id) return;
    try {
      const job = await api.getJob(id);
      knownJobs.remember(job.id, { goal: (job.goal || "").slice(0, 80) });
      addId.value = "";
      await renderList();
      toast("Job added to tracking.", "ok");
    } catch (error) {
      if (error instanceof api.ApiError && error.status === 404) toast("No job with that ID on the server.", "warn");
      else reportError(error, "could not load job");
    }
  });

  const quotaBox = h("div", null, "Loading quota…");
  const refreshQuota = async () => {
    try {
      const quota = await api.getQuota();
      clear(quotaBox).append(
        h("div", { class: "kv-grid" },
          h("div", { class: "kv" }, h("span", { class: "kv-key" }, "UTC day"), h("code", null, quota.day)),
          h("div", { class: "kv" }, h("span", { class: "kv-key" }, "used"), `${quota.used} / ${quota.limit}`),
          h("div", { class: "kv" }, h("span", { class: "kv-key" }, "remaining"),
            h("span", { class: `badge badge-${quota.remaining === 0 ? "bad" : quota.remaining <= quota.limit * 0.1 ? "warn" : "ok"}` }, String(quota.remaining)))));
    } catch (error) {
      if (error instanceof api.ApiError && error.status === 403) {
        clear(quotaBox).append(h("p", { class: "muted" }, "GET /v1/quota requires the jobs:write scope; the current credential cannot read its quota."));
      } else if (error instanceof api.ApiError && error.status === 404) {
        clear(quotaBox).append(h("p", { class: "muted" }, "This server does not expose /v1/quota (pre-v0.20)."));
      } else {
        clear(quotaBox).append(h("p", { class: "bad-text" }, error.message));
      }
    }
  };
  const adminPrincipal = h("input", { class: "input", placeholder: "principal id (e.g. bootstrap, oidc:sub)", style: "max-width:18rem" });
  const adminLimit = h("input", { class: "input", type: "number", min: 1, max: 1000000, placeholder: "daily jobs", style: "max-width:10rem" });
  const adminSet = h("button", { class: "button button-quiet", type: "button" }, "Set quota");
  const adminResult = h("span", { class: "muted small" });
  adminSet.addEventListener("click", async () => {
    const principal = adminPrincipal.value.trim();
    const limit = Number(adminLimit.value);
    if (!principal || !Number.isInteger(limit) || limit < 1) {
      toast("Principal id and a positive integer daily limit are required.", "warn");
      return;
    }
    adminSet.disabled = true;
    try {
      const status = await api.setQuota(principal, limit);
      adminResult.textContent = ` ${status.day}: ${status.used}/${status.limit} used (${status.remaining} remaining). Recorded in the audit chain as quota.update.`;
      toast("Quota updated.", "ok");
      refreshQuota();
    } catch (error) {
      if (error instanceof api.ApiError && (error.status === 401 || error.status === 403)) {
        toast("Quota overrides require the admin scope.", "bad");
      } else if (error instanceof api.ApiError && error.status === 404) {
        toast("This server does not expose quota overrides (pre-v0.20).", "warn");
      } else reportError(error, "quota update failed");
    } finally { adminSet.disabled = false; }
  });

  root.append(
    h("section", { class: "card" },
      h("h2", null, "Daily job quota"),
      quotaBox,
      h("h3", null, "Admin: set a principal's quota"),
      h("p", { class: "muted" }, "Requires the admin scope. Overrides are per principal, reset at midnight UTC, and audited."),
      h("div", { class: "row" }, adminPrincipal, adminLimit, adminSet, adminResult)),
    h("section", { class: "card" },
      h("h2", null, "New job"),
      h("p", { class: "muted" }, "Requires jobs:write. Leave run_at empty to queue for immediate execution by a worker."),
      goal,
      h("label", { class: "field-label", for: "job-run-at" }, "Run at (optional, local time)"),
      runAt,
      h("div", { class: "row" }, create, idemLabel)),
    h("section", { class: "card" },
      h("h2", null, "Tracked jobs (this browser)"),
      missingNote("No job listing endpoint", "GET /v1/jobs does not exist. The console polls each tracked job by ID against the server; the ID list itself lives in this browser only."),
      h("div", { class: "row" }, addId, addButton),
      listBox),
    detailHost);

  await renderList();
  refreshQuota();
  pollTimer = setInterval(() => {
    if (document.hidden) return;
    renderList();
  }, 10000);
  window.addEventListener("hashchange", () => {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
    if (activeStream) { activeStream.close(); activeStream = null; }
  }, { once: true });
}
