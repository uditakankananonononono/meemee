// Audit-chain viewer: cursor-paged entries, server verification status,
// an independent in-browser recomputation of the full SHA-256 chain, and a
// persisted verification anchor so a later visit detects a chain that was
// rolled back or rewritten underneath a previously verified head.
import { h, clear, toast, missingNote, jsonBlock, fullTime, shortHash, badge } from "../dom.js";
import * as api from "../api.js";
import { fetchFullChain, verifyChain } from "../verify.js";
import { auditAnchor } from "../store.js";
import { reportError } from "../app.js";

export async function renderAudit(root) {
  const statusBox = h("div", { class: "audit-status" });
  const anchorBox = h("div");
  const verifyBox = h("div");
  const summaryBox = h("div");
  const tableBox = h("div");
  const filterAction = h("input", { class: "input", placeholder: "Filter action (e.g. token.create)", style: "max-width:16rem" });
  const filterActor = h("input", { class: "input", placeholder: "Filter actor", style: "max-width:12rem" });
  const filterText = h("input", { class: "input", placeholder: "Search metadata/resource", style: "max-width:16rem" });

  let entries = [];
  let serverVerified = null;
  let verifiedThrough = 0; // highest sequence proven intact in this session

  const filtered = () => entries.filter((entry) => {
    if (filterAction.value.trim() && !entry.action.includes(filterAction.value.trim())) return false;
    if (filterActor.value.trim() && !entry.actor_id.includes(filterActor.value.trim())) return false;
    const needle = filterText.value.trim().toLowerCase();
    if (needle) {
      const hay = `${entry.resource} ${JSON.stringify(entry.metadata)}`.toLowerCase();
      if (!hay.includes(needle)) return false;
    }
    return true;
  });

  const renderSummary = () => {
    clear(summaryBox);
    if (!entries.length) return;
    const byAction = new Map();
    const byActor = new Map();
    for (const entry of entries) {
      byAction.set(entry.action, (byAction.get(entry.action) || 0) + 1);
      byActor.set(entry.actor_id, (byActor.get(entry.actor_id) || 0) + 1);
    }
    const chips = (map) => [...map.entries()]
      .sort((a, b) => b[1] - a[1])
      .map(([key, count]) => h("span", { class: "badge badge-muted" }, `${key} x${count}`));
    summaryBox.append(
      h("div", { class: "row" }, h("span", { class: "muted" }, "actions:"), chips(byAction)),
      h("div", { class: "row" }, h("span", { class: "muted" }, "actors:"), chips(byActor)));
  };

  const renderAnchor = () => {
    clear(anchorBox);
    const anchor = auditAnchor.get();
    if (!anchor) {
      anchorBox.append(h("p", { class: "muted small" },
        "No verified anchor stored in this browser yet. Run the in-browser verification to pin the chain head; later visits will detect rollback or rewriting below that head."));
      return;
    }
    anchorBox.append(h("p", { class: "muted small" },
      `Pinned anchor: sequence #${anchor.sequence}, head `,
      h("code", null, shortHash(anchor.head, 16, 8)),
      `, verified ${fullTime(anchor.verifiedAt)}.`));
  };

  const renderTable = () => {
    const rows = filtered();
    clear(tableBox);
    if (!rows.length) {
      tableBox.append(h("p", { class: "muted" }, entries.length ? "No entries match the filters." : "No entries loaded."));
      return;
    }
    const body = h("tbody", null);
    for (const entry of rows) {
      const verified = entry.sequence <= verifiedThrough;
      body.append(h("tr", null,
        h("td", { class: "muted" },
          `#${entry.sequence}`,
          verified ? h("span", { class: "badge badge-ok small", title: "hash verified in this browser" }, " hash ok") : null),
        h("td", { class: "muted nowrap" }, fullTime(entry.occurred_at)),
        h("td", null, h("code", null, entry.actor_id)),
        h("td", null, h("code", null, entry.action)),
        h("td", { class: "resource" }, h("code", null, entry.resource)),
        h("td", null, badge(entry.outcome, entry.outcome === "success" ? "ok" : "warn")),
        h("td", null, Object.keys(entry.metadata || {}).length
          ? h("details", null, h("summary", { class: "muted" }, "metadata"), jsonBlock(entry.metadata))
          : h("span", { class: "muted" }, "-")),
        h("td", null, h("code", { class: "small", title: entry.entry_hash }, shortHash(entry.entry_hash)))));
    }
    tableBox.append(
      h("p", { class: "muted small" }, `${rows.length} of ${entries.length} entries shown, oldest first.`),
      h("div", { class: "scroll-x" }, h("table", { class: "table" },
        h("thead", null, h("tr", null,
          h("th", null, "Seq"), h("th", null, "At"), h("th", null, "Actor"), h("th", null, "Action"),
          h("th", null, "Resource"), h("th", null, "Outcome"), h("th", null, "Metadata"), h("th", null, "Entry hash"))),
        body)));
  };
  for (const input of [filterAction, filterActor, filterText]) {
    input.addEventListener("input", renderTable);
  }

  const renderStatus = () => {
    clear(statusBox);
    if (serverVerified === null) return;
    if (serverVerified.ok) {
      statusBox.append(h("div", { class: "verify verify-ok" },
        h("strong", null, "Server verification: intact. "),
        "GET /v1/audit re-verifies the whole chain before answering and reported success."));
    } else {
      statusBox.append(h("div", { class: "verify verify-bad" },
        h("strong", null, "Server verification FAILED. "),
        serverVerified.message));
    }
  };

  // Compare the fetched chain against the pinned anchor before trusting it.
  // Returns an anchor verdict element, or null when no anchor exists.
  const anchorVerdict = (chain) => {
    const anchor = auditAnchor.get();
    if (!anchor) return null;
    const match = chain.find((entry) => entry.sequence === anchor.sequence);
    if (!match) {
      return h("div", { class: "verify verify-bad" },
        h("strong", null, "Anchor check FAILED: "),
        `the chain now ends before the previously verified head (#${anchor.sequence}). It was rolled back or replaced since ${fullTime(anchor.verifiedAt)}.`);
    }
    if (match.entry_hash !== anchor.head || (anchor.sequence > 1 && chain[anchor.sequence - 2] && chain[anchor.sequence - 2].entry_hash !== match.previous_hash)) {
      return h("div", { class: "verify verify-bad" },
        h("strong", null, "Anchor check FAILED: "),
        `entry #${anchor.sequence} no longer matches the hash this browser verified on ${fullTime(anchor.verifiedAt)}. History below the head was rewritten.`);
    }
    const added = chain.length - anchor.sequence;
    return h("div", { class: "verify verify-ok" },
      h("strong", null, "Anchor check: extends the pinned head. "),
      added > 0 ? `${added} new entr${added === 1 ? "y" : "ies"} since #${anchor.sequence}.` : "No new entries since the pinned head.");
  };

  const loadLatest = async () => {
    clear(verifyBox);
    tableBox.append(h("p", { class: "muted" }, "Loading…"));
    try {
      // The cursor is forward-only, so load from genesis; the chain is
      // append-only and small by design.
      const first = await api.listAudit(0, 500);
      entries = first.entries || [];
      let after = entries.length ? entries[entries.length - 1].sequence : 0;
      while (entries.length % 500 === 0 && entries.length) {
        const page = await api.listAudit(after, 500);
        if (!page.entries.length) break;
        entries.push(...page.entries);
        after = page.entries[page.entries.length - 1].sequence;
      }
      serverVerified = { ok: true };
    } catch (error) {
      if (error instanceof api.ApiError && error.status === 500 && /verification failed/.test(error.detail)) {
        serverVerified = { ok: false, message: error.detail };
        entries = [];
        toast("The server reports a broken audit chain and refused to serve entries.", "bad", 9000);
      } else if (error instanceof api.ApiError && (error.status === 401 || error.status === 403)) {
        serverVerified = null;
        clear(tableBox).append(h("p", { class: "muted" }, "The audit chain requires the admin scope."));
        renderStatus();
        return;
      } else {
        reportError(error, "could not load audit entries");
        return;
      }
    }
    renderStatus();
    renderSummary();
    renderTable();
    clear(anchorBox);
    const verdict = anchorVerdict(entries);
    if (verdict) anchorBox.append(verdict); else renderAnchor();
  };

  const verifyButton = h("button", { class: "button", type: "button" }, "Verify chain in this browser");
  verifyButton.addEventListener("click", async () => {
    verifyButton.disabled = true;
    clear(verifyBox).append(h("p", { class: "muted" }, "Fetching the full chain from genesis…"));
    try {
      const chain = await fetchFullChain({
        onProgress: (n) => { verifyBox.textContent = `Fetched ${n} entries…`; },
      });
      if (!chain.length) {
        clear(verifyBox).append(h("p", { class: "muted" }, "The chain is empty; nothing to verify."));
        return;
      }
      verifyBox.textContent = `Recomputing ${chain.length} entry hashes…`;
      const result = await verifyChain(chain, {
        onProgress: (n) => { verifyBox.textContent = `Verified ${n} of ${chain.length}…`; },
      });
      clear(verifyBox);
      if (result.ok) {
        verifiedThrough = result.checked;
        auditAnchor.set({
          sequence: chain[chain.length - 1].sequence,
          head: result.head,
          verifiedAt: new Date().toISOString(),
        });
        verifyBox.append(h("div", { class: "verify verify-ok" },
          h("strong", null, `Client verification: intact (${result.checked} entries recomputed). `),
          "Chain head ", h("code", null, shortHash(result.head, 16, 8)),
          " pinned as this browser's anchor. Recomputed with SHA-256 over the canonical encoding used by meemee.audit."));
        renderTable();
        renderAnchor();
      } else {
        verifyBox.append(h("div", { class: "verify verify-bad" },
          h("strong", null, `Client verification FAILED at sequence ${result.brokenAt}. `),
          `Checked ${result.checked} entries before the break. Recorded hash `,
          h("code", null, shortHash(result.recorded, 16, 8)),
          " vs recomputed ", h("code", null, shortHash(result.expected, 16, 8)), "."));
      }
    } catch (error) {
      clear(verifyBox);
      if (error instanceof api.ApiError && error.status === 500) {
        verifyBox.append(h("div", { class: "verify verify-bad" },
          h("strong", null, "Client verification could not run: "),
          `the server refused to serve entries (${error.detail}).`));
      } else reportError(error, "client verification failed");
    } finally { verifyButton.disabled = false; }
  });

  const exportButton = h("button", { class: "button button-quiet", type: "button" }, "Export loaded entries (JSON)");
  exportButton.addEventListener("click", () => {
    const blob = new Blob([JSON.stringify(entries, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = h("a", { href: url, download: `meemee-audit-${Date.now()}.json` });
    document.body.append(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  });

  root.append(
    h("section", { class: "card" },
      h("h2", null, "Audit chain"),
      h("p", { class: "muted" },
        "Append-only, tamper-evident SHA-256 chain over runs, jobs and token administration. Requires the admin scope."),
      statusBox,
      anchorBox,
      h("div", { class: "row" },
        h("button", { class: "button", type: "button", onclick: () => loadLatest() }, "Load entries"),
        verifyButton, exportButton),
      verifyBox,
      summaryBox,
      missingNote("No server-side per-entry verification API",
        "The server verifies the chain only as a whole on every /v1/audit call; the in-browser verification above is the console's own independent recomputation over the same documented hash construction, and the pinned anchor carries it across sessions."),
      h("div", { class: "row" }, filterAction, filterActor, filterText),
      tableBox));
  renderAnchor();
  loadLatest();
}
