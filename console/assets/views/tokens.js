// Token administration: create one-time secrets and list/revoke safe server metadata.
import { h, clear, toast, fullTime, badge } from "../dom.js";
import * as api from "../api.js";
import { reportError } from "../app.js";

const KNOWN_SCOPES = [
  { value: "admin", note: "token administration, audit chain, metrics" },
  { value: "runs:write", note: "create synchronous agent runs" },
  { value: "jobs:read", note: "read job status, events and SSE streams" },
  { value: "jobs:write", note: "create and cancel queued jobs" },
  { value: "companion:read", note: "read companion users, facts, conversations and check-ins" },
  { value: "companion:write", note: "manage companion users, persona, facts, chat and check-ins" },
];

export async function renderTokens(root) {
  const name = h("input", { class: "input", maxlength: 100, placeholder: "Token name (e.g. ci-reader)" });
  const expiry = h("input", { class: "input", type: "datetime-local" });
  const scopeBoxes = KNOWN_SCOPES.map(({ value, note }) => {
    const box = h("input", { type: "checkbox", value });
    return { value, box, node: h("label", { class: "check" }, box, ` ${value} `, h("span", { class: "muted small" }, `- ${note}`)) };
  });
  const create = h("button", { class: "button", type: "button" }, "Create token");
  const revealBox = h("div");
  const registryBox = h("div");

  const renderRegistry = () => {
    clear(registryBox);
    api.listTokens().then(({ tokens: items }) => {
      if (!items.length) { registryBox.append(h("p", { class: "muted" }, "No API tokens yet.")); return; }
      registryBox.append(h("div", { class: "scroll-x" }, h("table", { class: "table" },
        h("thead", null, h("tr", null, h("th", null, "Name"), h("th", null, "ID"), h("th", null, "Scopes"), h("th", null, "Last used"), h("th", null, "State"), h("th", null, ""))),
        h("tbody", null, items.map((item) => h("tr", null,
          h("td", null, item.name), h("td", null, h("code", null, item.id)),
          h("td", { class: "muted" }, item.scopes.join(" ")),
          h("td", { class: "muted" }, item.last_used_at ? fullTime(item.last_used_at) : "never"),
          h("td", null, item.revoked_at ? badge("revoked", "warn") : badge("active", "ok")),
          h("td", null, item.revoked_at ? null : h("button", { class: "button button-quiet", type: "button", onclick: () => revoke(item.id) }, "Revoke"))))))));
    }).catch((error) => reportError(error, "could not list tokens"));
  };

  const revoke = async (id) => {
    if (!window.confirm(`Revoke token ${id}? This cannot be undone.`)) return;
    try {
      await api.revokeToken(id);
      toast("Token revoked.", "ok");
    } catch (error) {
      if (error instanceof api.ApiError && error.status === 404) {
        toast("Server says no active token with that ID (already revoked or never existed).", "warn");
      } else reportError(error, "revoke failed");
    }
    renderRegistry();
  };

  create.addEventListener("click", async () => {
    const tokenName = name.value.trim();
    const scopes = scopeBoxes.filter(({ box }) => box.checked).map(({ value }) => value);
    if (!tokenName) { toast("Token name is required.", "warn"); return; }
    if (!scopes.length) { toast("Pick at least one scope.", "warn"); return; }
    let expiresAt = null;
    if (expiry.value) {
      const when = new Date(expiry.value);
      if (Number.isNaN(when.getTime())) { toast("Expiry must be a valid date/time.", "warn"); return; }
      expiresAt = when.toISOString();
    }
    create.disabled = true;
    try {
      const created = await api.createToken(tokenName, scopes, expiresAt);
      clear(revealBox).append(
        h("div", { class: "reveal" },
          h("h3", null, "Token created - shown once"),
          h("p", { class: "bad-text" },
            created.warning || "Shown once; store it securely. The server stores only a SHA-256 digest and cannot show this value again."),
          h("div", { class: "row" },
            h("code", { class: "reveal-value" }, created.token),
            h("button", { class: "button button-quiet", type: "button", onclick: (e) => {
              navigator.clipboard.writeText(created.token)
                .then(() => { e.target.textContent = "Copied"; })
                .catch(() => toast("Clipboard unavailable; select and copy manually.", "warn"));
            } }, "Copy")),
          h("p", { class: "muted small" }, `id ${created.id} · metadata is available from the server list below.`)));
      toast("Token created.", "ok");
      name.value = ""; expiry.value = "";
      scopeBoxes.forEach(({ box }) => { box.checked = false; });
    } catch (error) {
      if (error instanceof api.ApiError && error.status === 403) toast("The current credential lacks the admin scope.", "bad");
      else reportError(error, "token creation failed");
    } finally { create.disabled = false; renderRegistry(); }
  });

  const revokeId = h("input", { class: "input", placeholder: "Token ID to revoke", style: "max-width:22rem" });
  const revokeButton = h("button", { class: "button button-danger", type: "button" }, "Revoke by ID");
  revokeButton.addEventListener("click", () => {
    const id = revokeId.value.trim();
    if (!id) return;
    revoke(id).then(() => { revokeId.value = ""; });
  });

  root.append(
    h("section", { class: "card" },
      h("h2", null, "Create API token"),
      h("p", { class: "muted" }, "Requires the admin scope. Allowed scopes: admin, runs:write, jobs:read, jobs:write, companion:read, companion:write."),
      h("label", { class: "field-label" }, "Name"), name,
      h("label", { class: "field-label" }, "Scopes"), ...scopeBoxes.map(({ node }) => node),
      h("label", { class: "field-label" }, "Expires at (optional, local time)"), expiry,
      h("div", { class: "row" }, create),
      revealBox),
    h("section", { class: "card" },
      h("h2", null, "API tokens"), registryBox),
    h("section", { class: "card" },
      h("h2", null, "Revoke a token"),
      h("p", { class: "muted" }, "Revocation needs the token ID (the 24-hex-character identifier returned at creation)."),
      h("div", { class: "row" }, revokeId, revokeButton)));
  renderRegistry();
}
