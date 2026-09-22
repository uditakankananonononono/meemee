// Meemee operator console shell: hash router, session bar, shared context.
import { h, clear, toast, badge } from "./dom.js";
import * as api from "./api.js";
import { bearerToken, settings } from "./store.js";
import { renderStatus } from "./views/status.js";
import { renderRuns } from "./views/runs.js";
import { renderJobs } from "./views/jobs.js";
import { renderTokens } from "./views/tokens.js";
import { renderAudit } from "./views/audit.js";
import { renderPermissions } from "./views/permissions.js";

const VIEWS = {
  status: { title: "Status", render: renderStatus },
  runs: { title: "Runs", render: renderRuns },
  jobs: { title: "Jobs", render: renderJobs },
  tokens: { title: "Tokens", render: renderTokens },
  audit: { title: "Audit chain", render: renderAudit },
  permissions: { title: "Permissions", render: renderPermissions },
};

const ctx = {
  caps: null,
  capsError: null,
  async refreshCaps() {
    try {
      ctx.caps = await api.probeScopes();
      ctx.capsError = null;
    } catch (error) {
      ctx.caps = null;
      ctx.capsError = error;
    }
    renderSessionChip();
    return ctx.caps;
  },
};

export function reportError(error, prefix = "request failed") {
  if (error && error.name === "AbortError") return;
  if (error instanceof api.ApiError && error.status === 429) {
    const wait = error.retryAfter ? ` Retry after ${error.retryAfter}s.` : "";
    toast(`API rate limit reached.${wait} Console polling pauses automatically; slow down manual refreshes.`, "warn", 8000);
    return;
  }
  const suffix = error && error.requestId ? ` (request ${error.requestId})` : "";
  toast(`${prefix}: ${error ? error.message : "unknown error"}${suffix}`, "bad", 7000);
}

function currentView() {
  const name = (window.location.hash.replace(/^#\/?/, "") || "status").split("/")[0];
  return VIEWS[name] ? name : "status";
}

function renderSessionChip() {
  const chip = document.getElementById("session-chip");
  if (!chip) return;
  clear(chip);
  const caps = ctx.caps;
  const token = bearerToken.get();
  if (ctx.capsError) {
    chip.append(badge("server unreachable", "bad"));
  } else if (!caps) {
    chip.append(badge("checking session…", "muted"));
  } else if (!caps.authenticated) {
    chip.append(badge("signed out", "muted"));
  } else {
    const scopes = [];
    if (caps.admin) scopes.push("admin");
    if (caps.runsWrite) scopes.push("runs:write");
    if (caps.jobsRead) scopes.push("jobs:read");
    if (caps.jobsWrite) scopes.push("jobs:write");
    chip.append(badge(scopes.length ? scopes.join(" · ") : "authenticated (no known scopes)", caps.admin ? "ok" : "info"));
  }
  if (token) chip.append(badge("bearer token set", "info"));
}

function sessionPanel() {
  const dialog = h("dialog", { id: "session-dialog", class: "dialog" });
  const tokenInput = h("input", {
    type: "password", id: "token-input", class: "input", autocomplete: "off",
    placeholder: "mee_… scoped API token, bootstrap token, or OIDC access token",
  });
  const remember = h("input", { type: "checkbox", id: "remember-token" });
  tokenInput.value = bearerToken.get();
  remember.checked = settings.get().rememberToken;
  dialog.append(
    h("form", { method: "dialog", class: "dialog-body" },
      h("h2", null, "Console credential"),
      h("p", { class: "muted" },
        "Two ways in: sign in with the OIDC provider (sets the meemee_session cookie), ",
        "or paste a bearer token. Both are tried on every API call."),
      h("div", { class: "row" },
        h("a", { class: "button", href: api.loginUrl() }, "Sign in with OIDC"),
        h("button", { class: "button button-quiet", type: "button", onclick: async () => {
          await api.logout();
          bearerToken.clear();
          toast("Session cookie cleared.", "ok");
          await ctx.refreshCaps();
          route();
        } }, "Sign out")),
      h("label", { class: "field-label", for: "token-input" }, "Bearer token"),
      tokenInput,
      h("label", { class: "check" }, remember, " remember in this browser (localStorage). Off = tab memory only (sessionStorage)."),
      h("div", { class: "row" },
        h("button", { class: "button", type: "button", onclick: async () => {
          const value = tokenInput.value.trim();
          bearerToken.set(value, remember.checked);
          settings.set({ rememberToken: remember.checked });
          dialog.close();
          toast(value ? "Bearer token stored for this console." : "Bearer token cleared.", "ok");
          await ctx.refreshCaps();
          route();
        } }, "Apply"),
        h("button", { class: "button button-quiet", value: "cancel" }, "Close")),
      h("p", { class: "muted small" },
        "The token is sent only to ", h("code", null, api.apiBase()), ". Never paste a token into a console served from an origin you do not control.")),
  );
  return dialog;
}

function renderNav(active) {
  const nav = document.getElementById("nav-links");
  clear(nav);
  for (const [name, view] of Object.entries(VIEWS)) {
    nav.append(h("a", { href: `#/${name}`, class: name === active ? "active" : "" }, view.title));
  }
}

async function route() {
  const active = currentView();
  renderNav(active);
  document.title = `Meemee Console - ${VIEWS[active].title}`;
  const main = document.getElementById("view");
  clear(main);
  await VIEWS[active].render(main, ctx);
  renderSessionChip();
}

window.addEventListener("hashchange", route);

document.addEventListener("DOMContentLoaded", async () => {
  document.body.append(sessionPanel());
  document.getElementById("session-button").addEventListener("click", () => {
    document.getElementById("session-dialog").showModal();
  });
  document.getElementById("api-base-label").textContent = api.apiBase();
  await ctx.refreshCaps();
  route();
});
