// Browser-local persistence. The Meemee API intentionally exposes no list
// endpoints for jobs, runs or tokens, so the console keeps its own registry
// of the objects created or followed from this browser. This is a client
// convenience only; the API remains the source of truth for object state.
const LS = window.localStorage;
const SS = window.sessionStorage;

function readJson(storage, key, fallback) {
  try {
    const raw = storage.getItem(key);
    return raw ? JSON.parse(raw) : fallback;
  } catch {
    return fallback;
  }
}

function writeJson(storage, key, value) {
  try { storage.setItem(key, JSON.stringify(value)); } catch { /* quota or privacy mode */ }
}

const SETTINGS_KEY = "meemee.console.settings";
const JOBS_KEY = "meemee.console.jobs";
const TOKENS_KEY = "meemee.console.tokens";
const RUNS_KEY = "meemee.console.runs";
const TOKEN_SESSION_KEY = "meemee.console.token";
const TOKEN_LOCAL_KEY = "meemee.console.token.saved";

export const settings = {
  get() {
    return readJson(LS, SETTINGS_KEY, { apiBase: "", rememberToken: false });
  },
  set(patch) {
    writeJson(LS, SETTINGS_KEY, { ...this.get(), ...patch });
  },
};

export const bearerToken = {
  get() {
    return SS.getItem(TOKEN_SESSION_KEY) || LS.getItem(TOKEN_LOCAL_KEY) || "";
  },
  set(token, remember) {
    if (token) {
      SS.setItem(TOKEN_SESSION_KEY, token);
      if (remember) LS.setItem(TOKEN_LOCAL_KEY, token);
      else LS.removeItem(TOKEN_LOCAL_KEY);
    } else {
      SS.removeItem(TOKEN_SESSION_KEY);
      LS.removeItem(TOKEN_LOCAL_KEY);
    }
  },
  clear() { this.set(""); },
};

export const knownJobs = {
  all() { return readJson(LS, JOBS_KEY, []); },
  remember(id, meta = {}) {
    const list = this.all().filter((entry) => entry.id !== id);
    list.unshift({ id, addedAt: new Date().toISOString(), ...meta });
    writeJson(LS, JOBS_KEY, list.slice(0, 200));
  },
  forget(id) {
    writeJson(LS, JOBS_KEY, this.all().filter((entry) => entry.id !== id));
  },
};

export const tokenRegistry = {
  all() { return readJson(LS, TOKENS_KEY, []); },
  remember(record) {
    // Never persist the secret token value, only its metadata.
    const { id, name, scopes, expires_at: expiresAt } = record;
    const list = this.all().filter((entry) => entry.id !== id);
    list.unshift({ id, name, scopes, expiresAt, createdAt: new Date().toISOString(), revoked: false });
    writeJson(LS, TOKENS_KEY, list.slice(0, 100));
  },
  markRevoked(id) {
    const list = this.all();
    for (const entry of list) if (entry.id === id) entry.revoked = true;
    writeJson(LS, TOKENS_KEY, list);
  },
  forget(id) {
    writeJson(LS, TOKENS_KEY, this.all().filter((entry) => entry.id !== id));
  },
};

// Last audit-chain head this browser independently verified. Comparing a
// future chain against this anchor detects rollback or rewriting between
// visits - the tamper-evidence property, carried across sessions.
const ANCHOR_KEY = "meemee.console.auditAnchor";

export const auditAnchor = {
  get() { return readJson(LS, ANCHOR_KEY, null); },
  set(anchor) { writeJson(LS, ANCHOR_KEY, anchor); },
  clear() { try { LS.removeItem(ANCHOR_KEY); } catch { /* ignore */ } },
};

export const runHistory = {
  all() { return readJson(LS, RUNS_KEY, []); },
  remember(report) {
    const list = this.all().filter((entry) => entry.run_id !== report.run_id);
    list.unshift({ ...report, recordedAt: new Date().toISOString() });
    writeJson(LS, RUNS_KEY, list.slice(0, 100));
  },
  forget(id) {
    writeJson(LS, RUNS_KEY, this.all().filter((entry) => entry.run_id !== id));
  },
};
