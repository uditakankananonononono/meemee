export interface HealthStatus { status: string; version: string }
export interface ReadinessComponent { ok: boolean; error?: string; free_bytes?: number; minimum_bytes?: number; status?: number; [key: string]: unknown }
export interface ReadinessStatus { status: "ready" | "not_ready" | string; components?: Record<string, ReadinessComponent> }
export interface RunReport { run_id: string; goal: string; final: string; steps_used: number; tool_results: Record<string, unknown>[] }
export type JobStatus = "queued" | "running" | "done" | "failed" | "cancel_requested" | "cancelled";
export const TERMINAL_JOB_STATUSES: ReadonlySet<JobStatus> = new Set(["done", "failed", "cancelled"]);
export const TERMINAL_EVENT_KINDS: ReadonlySet<string> = new Set(["done", "failed", "cancelled"]);
export interface Quota { day: string; used: number; limit: number; remaining: number }
export interface CreatedJob { id: string; quota: Quota }
export interface Job { id: string; goal: string; run_at: string; status: JobStatus; attempts: number; max_attempts: number; result: string | null; error: string | null; created_at: string; updated_at: string }
export interface JobCancelResult { id: string; status: JobStatus }
export interface JobEvent { sequence: number; job_id: string; kind: string; payload: Record<string, unknown>; created_at: string }
export interface CreatedToken { id: string; token: string; warning: string }
export interface RevokedToken { id: string; revoked: boolean }
export interface AuditEntry { sequence: number; occurred_at: string; actor_id: string; action: string; resource: string; outcome: string; metadata: Record<string, unknown>; previous_hash: string; entry_hash: string }
export interface AuditPage { verified: boolean; entries: AuditEntry[] }
export interface RateLimitInfo { limit?: number; remaining?: number; reset?: Date }
export interface ResponseInfo { requestId?: string; rateLimit?: RateLimitInfo }
export const KNOWN_SCOPES = ["admin", "runs:write", "jobs:read", "jobs:write"] as const;
