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
  const result = { authenticated: false, admin: false, runsWrite: null, jobsRead: false, jobsWrite: false };
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
  ]);
  return result;
}

// Interactive login endpoints (web_login.py). The console links out for the
// redirect flow; logout is a POST that clears the session cookie.
export const loginUrl = () => apiUrl("/auth/login");
export async function logout() {
  await request("/auth/logout", { method: "POST" }).catch(() => null);
}
