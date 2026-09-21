// Independent client-side verification of the tamper-evident audit chain.
// Fetches the entire chain from genesis (paging with the documented
// after/limit cursor, 500 entries per page server-side cap) and recomputes
// every link. The server's /v1/audit response already refuses to serve a
// broken chain (500 with the failing sequence), so this is a second,
// independent opinion produced in the browser.
import { listAudit } from "./api.js";
import { auditEntryHash, GENESIS_HASH } from "./canonical.js";

export async function fetchFullChain({ onProgress, signal } = {}) {
  const entries = [];
  let after = 0;
  for (;;) {
    const page = await listAudit(after, 500, { signal });
    const batch = page.entries || [];
    entries.push(...batch);
    if (onProgress) onProgress(entries.length);
    if (batch.length < 500) break;
    after = batch[batch.length - 1].sequence;
  }
  return entries;
}

export async function verifyChain(entries, { onProgress } = {}) {
  let previous = GENESIS_HASH;
  let checked = 0;
  for (const entry of entries) {
    const computed = await auditEntryHash(entry, previous);
    if (entry.previous_hash !== previous || entry.entry_hash !== computed) {
      return {
        ok: false,
        brokenAt: entry.sequence,
        checked,
        expected: computed,
        recorded: entry.entry_hash,
        head: previous,
      };
    }
    previous = entry.entry_hash;
    checked += 1;
    if (onProgress && checked % 200 === 0) onProgress(checked);
  }
  return { ok: true, checked, head: previous };
}
