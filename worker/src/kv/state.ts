/**
 * KV state management — typed helpers for Worker state.
 *
 * Key patterns:
 *   delivery:{id}           → "1"              (24h TTL, idempotency)
 *   token:installation      → token string     (50min TTL, cache)
 *   pr:{number}:status      → JSON             (4h TTL, evaluation state)
 *   triage:daily:{date}     → count string     (48h TTL, rate limit)
 *   branch:cleanup:{date}   → JSON             (7d TTL, audit trail)
 */

import { KV_TTL } from "../util/constants";

// ── Idempotency ─────────────────────────────────────────────────────

/**
 * Check if a webhook delivery has already been processed.
 */
export async function isDuplicate(
  kv: KVNamespace,
  deliveryId: string | null,
): Promise<boolean> {
  if (!deliveryId) return false;

  const key = `delivery:${deliveryId}`;
  const existing = await kv.get(key);

  if (existing) return true;

  // Mark as processed
  await kv.put(key, "1", { expirationTtl: KV_TTL.DELIVERY });
  return false;
}

// ── PR Status Tracking ──────────────────────────────────────────────

export interface PRStatus {
  sha: string;
  lastEval: string; // ISO timestamp
  checksComplete: number;
  checksTotal: number;
  allPassed: boolean;
  merged: boolean;
}

/**
 * Get the cached evaluation status for a PR.
 */
export async function getPRStatus(
  kv: KVNamespace,
  prNumber: number,
): Promise<PRStatus | null> {
  const key = `pr:${prNumber}:status`;
  const data = await kv.get(key, "json");
  return data as PRStatus | null;
}

/**
 * Update the evaluation status for a PR.
 */
export async function setPRStatus(
  kv: KVNamespace,
  prNumber: number,
  status: PRStatus,
): Promise<void> {
  const key = `pr:${prNumber}:status`;
  await kv.put(key, JSON.stringify(status), { expirationTtl: KV_TTL.PR_STATUS });
}

/**
 * Clean up PR status after merge or close.
 */
export async function clearPRStatus(
  kv: KVNamespace,
  prNumber: number,
): Promise<void> {
  await kv.delete(`pr:${prNumber}:status`);
}

// ── Triage Rate Limiting ────────────────────────────────────────────

/**
 * Get the current daily triage request count.
 */
export async function getTriageCount(kv: KVNamespace): Promise<number> {
  const today = new Date().toISOString().slice(0, 10); // YYYY-MM-DD
  const key = `triage:daily:${today}`;
  const count = await kv.get(key);
  return count ? parseInt(count, 10) : 0;
}

/**
 * Increment the daily triage request count.
 */
export async function incrementTriageCount(kv: KVNamespace): Promise<number> {
  const today = new Date().toISOString().slice(0, 10);
  const key = `triage:daily:${today}`;
  const current = await getTriageCount(kv);
  const newCount = current + 1;
  await kv.put(key, newCount.toString(), { expirationTtl: KV_TTL.TRIAGE_DAILY });
  return newCount;
}

// ── Auto-Merge Status ──────────────────────────────────────────────

/**
 * Set the auto-merge status for a PR.
 */
export async function setAutoMergeStatus(
  kv: KVNamespace,
  prNumber: number,
  enabled: boolean,
): Promise<void> {
  const key = `pr:${prNumber}:auto_merge`;
  await kv.put(key, enabled ? "enabled" : "disabled", { expirationTtl: KV_TTL.PR_STATUS });
}

/**
 * Get the auto-merge status for a PR.
 */
export async function getAutoMergeStatus(
  kv: KVNamespace,
  prNumber: number,
): Promise<"enabled" | "disabled" | null> {
  const key = `pr:${prNumber}:auto_merge`;
  const value = await kv.get(key);
  if (value === "enabled" || value === "disabled") return value;
  return null;
}

// ── Comment Dedup ──────────────────────────────────────────────────

/**
 * Check if a failure comment has already been posted for this PR+SHA.
 */
export async function hasCommented(
  kv: KVNamespace,
  prNumber: number,
  sha: string,
): Promise<boolean> {
  const key = `pr:${prNumber}:comment:${sha}`;
  return (await kv.get(key)) !== null;
}

/**
 * Mark that a failure comment has been posted for this PR+SHA.
 */
export async function markCommented(
  kv: KVNamespace,
  prNumber: number,
  sha: string,
): Promise<void> {
  const key = `pr:${prNumber}:comment:${sha}`;
  await kv.put(key, "1", { expirationTtl: KV_TTL.PR_STATUS });
}

// ── Branch Cleanup Audit ────────────────────────────────────────────

export interface CleanupLog {
  deleted: string[];
  skipped: string[];
  timestamp: string;
}

/**
 * Log a branch cleanup operation for auditing.
 */
export async function logCleanup(
  kv: KVNamespace,
  log: CleanupLog,
): Promise<void> {
  const today = new Date().toISOString().slice(0, 10);
  const key = `branch:cleanup:${today}`;
  await kv.put(key, JSON.stringify(log), { expirationTtl: KV_TTL.CLEANUP_LOG });
}
