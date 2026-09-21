// Canonical JSON encoding compatible with CPython's
// json.dumps(value, sort_keys=True, separators=(",", ":")) (ensure_ascii=True).
// The Meemee audit chain hashes this exact encoding, so an independent
// client-side verification must reproduce it byte-for-byte.

function escapeString(value) {
  // JSON.stringify matches Python's escaping for ASCII and control
  // characters; Python additionally escapes every non-ASCII code point as
  // XXXX (astral characters become surrogate pairs), so apply that on top.
  return JSON.stringify(value).replace(/[\u0080-\uFFFF]/g, (ch) => {
    const code = ch.codePointAt(0);
    return `\\u${code.toString(16).padStart(4, "0")}`;
  });
}

function encodeNumber(value) {
  if (!Number.isFinite(value)) {
    // Python json.dumps emits Infinity/-Infinity/NaN; JSON would emit null.
    if (Number.isNaN(value)) return "NaN";
    return value > 0 ? "Infinity" : "-Infinity";
  }
  return JSON.stringify(value);
}

export function canonicalJson(value) {
  if (value === null || value === undefined) return "null";
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "number") return encodeNumber(value);
  if (typeof value === "string") return escapeString(value);
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (typeof value === "object") {
    const keys = Object.keys(value).sort();
    const parts = keys.map((key) => `${escapeString(key)}:${canonicalJson(value[key])}`);
    return `{${parts.join(",")}}`;
  }
  throw new Error(`cannot canonicalize value of type ${typeof value}`);
}

export async function sha256Hex(text) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
}

const SEP = "\x1f";

// Mirrors meemee.audit.AuditLog.hash: sha256 of the unit-separator joined
// fields over the canonical metadata encoding.
export async function auditEntryHash(entry, previousHash) {
  const canonical = [
    previousHash,
    entry.occurred_at,
    entry.actor_id,
    entry.action,
    entry.resource,
    entry.outcome,
    canonicalJson(entry.metadata ?? {}),
  ].join(SEP);
  return sha256Hex(canonical);
}

export const GENESIS_HASH = "0".repeat(64);
