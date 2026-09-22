// Thin, explicit client over the documented Meemee HTTP surface.
// Auth: Authorization bearer header when a token is configured, plus the
// meemee_session cookie set by the interactive OIDC flow (same-origin).
import { bearerToken, settings } from "./store.js";

export class ApiError extends Error {
  constructor(status, detail, { retryAfter = null, requestId = null } = {}) {
    super(detail || `HTTP ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail || `HTTP ${status}`;
    this.retryAfter = retryAfter;
    this.requestId = requestId;
  }
}

export function apiBase() {
  const configured = (settings.get().apiBase || "").trim().replace(/\/+$/, "");
  return configured || window.location.origin;
}

export function apiUrl(path) {
  return `${apiBase()}${path}`;
}

async function request(path, { method = "GET", body, headers = {}, signal } = {}) {
  const finalHeaders = { accept: "application/json", ...headers };
  const token = bearerToken.get();
  if (token) finalHeaders.authorization = `Bearer ${token}`;
  let payload;
  if (body !== undefined) {
    finalHeaders["content-type"] = "application/json";
    payload = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(apiUrl(path), {
      method, headers: finalHeaders, body: payload,
      credentials: "same-origin", signal, redirect: "follow",
    });
  } catch (error) {
    if (error.name === "AbortError") throw error;
    throw new ApiError(0, `network error: ${error.message}`);
  }
  const requestId = response.headers.get("x-request-id");
  if (response.status === 429) {
    const retry = Number(response.headers.get("retry-after")) || null;
    throw new ApiError(429, "rate limited by the API", { retryAfter: retry, requestId });
  }
  const text = await response.text();
  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    if (text) {
      try {
        const parsed = JSON.parse(text);
        if (typeof parsed.detail === "string") detail = parsed.detail;
        else if (Array.isArray(parsed.detail)) {
          detail = parsed.detail.map((d) => d.msg || JSON.stringify(d)).join("; ");
        }
      } catch { detail = text.slice(0, 300); }
    }
    throw new ApiError(response.status, detail, { requestId });
  }
  if (!text) return null;
  const type = response.headers.get("content-type") || "";
  if (type.includes("application/json")) {
    try { return JSON.parse(text); } catch { throw new ApiError(response.status, "invalid JSON in response"); }
  }
  return text;
}

// --- Unauthenticated probes -------------------------------------------------
export const getHealth = (opts) => request("/health", opts);
export const getReady = (opts) => request("/ready", opts);
export const getWhoami = (opts) => request("/v1/whoami", opts);

// --- Runs (scope: runs:write) ------------------------------------------------
export const createRun = (goal, approveWrites, opts) =>
  request("/v1/runs", { method: "POST", body: { goal, approve_writes: approveWrites }, ...opts });

// --- Jobs (scopes: jobs:read / jobs:write) -----------------------------------
export const createJob = (goal, runAt, opts) =>
  request("/v1/jobs", { method: "POST", body: runAt ? { goal, run_at: runAt } : { goal }, ...opts });
export const listJobs = (status = null, before = null, limit = 100, opts) => {
  const params = new URLSearchParams({ limit: String(limit) });
  if (status) params.set("status", status);
  if (before) params.set("before", before);
  return request(`/v1/jobs?${params}`, opts);
};
export const getJob = (id, opts) => request(`/v1/jobs/${encodeURIComponent(id)}`, opts);
export const cancelJob = (id, opts) =>
  request(`/v1/jobs/${encodeURIComponent(id)}`, { method: "DELETE", ...opts });
export const getJobEvents = (id, after = 0, opts) =>
  request(`/v1/jobs/${encodeURIComponent(id)}/events?after=${after}`, opts);

// --- Token administration (scope: admin) -------------------------------------
export const createToken = (name, scopes, expiresAt, opts) =>
  request("/v1/tokens", {
    method: "POST",
    body: expiresAt ? { name, scopes, expires_at: expiresAt } : { name, scopes },
    ...opts,
  });
export const listTokens = (revoked = null, before = null, limit = 100, opts) => {
  const params = new URLSearchParams({ limit: String(limit) });
  if (revoked !== null) params.set("revoked", String(revoked));
  if (before) params.set("before", before);
  return request(`/v1/tokens?${params}`, opts);
};
export const listRuns = (before = null, limit = 100, opts) => {
  const params = new URLSearchParams({ limit: String(limit) });
  if (before) params.set("before", before);
  return request(`/v1/runs?${params}`, opts);
};
export const getRun = (id, opts) => request(`/v1/runs/${encodeURIComponent(id)}`, opts);
export const revokeToken = (id, opts) =>
  request(`/v1/tokens/${encodeURIComponent(id)}`, { method: "DELETE", ...opts });

// --- Audit chain (scope: admin) ----------------------------------------------
export const listAudit = (after = 0, limit = 100, opts) =>
  request(`/v1/audit?after=${after}&limit=${limit}`, opts);

// --- Metrics (scope: admin) --------------------------------------------------
export const getMetrics = (opts) => request("/metrics", { ...opts, headers: { accept: "text/plain" } });

// --- Capability probes ---------------------------------------------------------
// The API exposes no whoami endpoint. These probes infer what the current
// credential can do using only side-effect-free calls:
//   - invalid payloads fail validation (422) before any write, so a 422
//     proves the scope was present; 401 means unauthenticated; 403 means the
//     scope is missing.
export async function probeScopes() {
  const result = { authenticated: false, admin: false, runsWrite: null, jobsRead: false, jobsWrite: false, companionRead: false, companionWrite: false };
  const apply = (key, promise, extra) =>
    promise.then(() => { result[key] = true; result.authenticated = true; })
      .catch((error) => {
        if (error instanceof ApiError && error.status === 422) {
          result[key] = true; result.authenticated = true;
        } else if (error instanceof ApiError && error.status === 403) {
          result[key] = extra ? extra : false; result.authenticated = true;
        } else if (error instanceof ApiError && error.status === 404) {
          result[key] = true; result.authenticated = true;
        } else if (error instanceof ApiError && error.status === 401) {
          result[key] = false;
        } else { throw error; }
      });
  await Promise.all([
    apply("admin", listAudit(0, 1)),
    apply("jobsRead", getJob("00000000000000000000000000000000")),
    apply("jobsWrite", cancelJob("00000000000000000000000000000000")),
    apply("runsWrite", createRun("", false)),
    apply("companionRead", getCompanionUser("__probe__")),
    apply("companionWrite", updateCompanionPersona("__probe__", {})),
  ]);
  return result;
}

// Interactive login endpoints (web_login.py). The console links out for the
// redirect flow; logout is a POST that clears the session cookie.
export const loginUrl = () => apiUrl("/auth/login");
export async function logout() {
  await request("/auth/logout", { method: "POST" }).catch(() => null);
}

// --- Persistent tool approvals (scope: admin) -------------------------------
export const listApprovals = (principal, opts) =>
  request(`/v1/approvals/${encodeURIComponent(principal)}`, opts);
export const grantApproval = (principal, tool, expiresAt, constraints, opts) =>
  request(`/v1/approvals/${encodeURIComponent(principal)}`, {
    method: "PUT", body: { tool, expires_at: expiresAt || null, argument_constraints: constraints || null }, ...opts,
  });
export const revokeApproval = (principal, tool, opts) =>
  request(`/v1/approvals/${encodeURIComponent(principal)}/${encodeURIComponent(tool)}`, { method: "DELETE", ...opts });

// --- Account administration (scope: admin) ---------------------------------
export const getQuotaForCurrentPrincipal = (opts) => request("/v1/quota", opts);
export const setPrincipalQuota = (principal, dailyJobs, opts) =>
  request(`/v1/quota/${encodeURIComponent(principal)}`, { method: "PUT", body: { daily_jobs: dailyJobs }, ...opts });
export const assignPrincipalPlan = (principal, plan, opts) =>
  request(`/v1/entitlements/${encodeURIComponent(principal)}`, { method: "PUT", body: { plan }, ...opts });

// --- Companion layer (scopes: companion:read / companion:write) --------------
export const listCompanionUsers = (limit = 100, opts) =>
  request(`/v1/companion/users?limit=${limit}`, opts);
export const getCompanionUser = (userId, opts) =>
  request(`/v1/companion/users/${encodeURIComponent(userId)}`, opts);
export const upsertCompanionUser = (userId, body, opts) =>
  request(`/v1/companion/users/${encodeURIComponent(userId)}`, { method: "PUT", body, ...opts });
export const updateCompanionPersona = (userId, persona, opts) =>
  request(`/v1/companion/users/${encodeURIComponent(userId)}/persona`, { method: "PUT", body: { persona }, ...opts });
export const updateCompanionCheckins = (userId, checkins, opts) =>
  request(`/v1/companion/users/${encodeURIComponent(userId)}/checkins`, { method: "PUT", body: { checkins }, ...opts });
export const listCompanionFacts = (userId, query = null, opts) => {
  const params = new URLSearchParams();
  if (query) params.set("query", query);
  const suffix = params.toString() ? `?${params}` : "";
  return request(`/v1/companion/users/${encodeURIComponent(userId)}/facts${suffix}`, opts);
};
export const addCompanionFact = (userId, category, text, opts) =>
  request(`/v1/companion/users/${encodeURIComponent(userId)}/facts`, {
    method: "POST", body: { category, text, confidence: 1.0 }, ...opts,
  });
export const retireCompanionFact = (userId, factId, opts) =>
  request(`/v1/companion/users/${encodeURIComponent(userId)}/facts/${encodeURIComponent(factId)}`, { method: "DELETE", ...opts });
export const companionChat = (userId, text, conversationId = null, opts) =>
  request("/v1/companion/chat", {
    method: "POST",
    body: conversationId
      ? { user_id: userId, text, channel: "local", conversation_id: conversationId }
      : { user_id: userId, text, channel: "local" },
    ...opts,
  });
export const listCompanionConversations = (userId, opts) =>
  request(`/v1/companion/users/${encodeURIComponent(userId)}/conversations`, opts);
export const getCompanionMessages = (conversationId, limit = 100, opts) =>
  request(`/v1/companion/conversations/${encodeURIComponent(conversationId)}/messages?limit=${limit}`, opts);
export const planCompanionCheckin = (userId, opts) =>
  request(`/v1/companion/users/${encodeURIComponent(userId)}/checkins/plan`, { method: "POST", ...opts });
export const listCompanionCheckins = (userId, status = null, opts) => {
  const params = new URLSearchParams();
  if (status) params.set("status", status);
  const suffix = params.toString() ? `?${params}` : "";
  return request(`/v1/companion/users/${encodeURIComponent(userId)}/checkins${suffix}`, opts);
};
