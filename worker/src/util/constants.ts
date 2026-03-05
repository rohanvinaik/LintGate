/**
 * Constants ported from ship_main.py — these are invariants.
 *
 * SUCCESS_CONCLUSIONS must exactly match the Python set:
 *   SUCCESS_CONCLUSIONS = {"success", "neutral", "skipped"}
 */

/** Check conclusions considered passing. */
export const SUCCESS_CONCLUSIONS = new Set(["success", "neutral", "skipped"]);

/** Branches that must never be deleted or have PRs auto-created against them as head. */
export const PROTECTED_BRANCHES = new Set(["main", "badges"]);

/** Default number of days before a merged branch is considered stale. */
export const STALE_BRANCH_DAYS = 30;

/** Days before an unmerged disposable branch is cleaned up. */
export const DISPOSABLE_STALE_DAYS = 7;

/** Branch prefixes that are safe to close+delete when stale and unmerged. */
export const DISPOSABLE_BRANCH_PREFIXES = ["dependabot/", "codex/ship-"];

/** GitHub API base URL. */
export const GITHUB_API = "https://api.github.com";

/** GitHub Models API endpoint (free tier). */
export const GITHUB_MODELS_API = "https://models.inference.ai.azure.com/chat/completions";

/** Daily rate limit for GitHub Models API calls (safety margin below 150). */
export const MODELS_DAILY_LIMIT = 140;

/** User-Agent header for GitHub API requests. */
export const USER_AGENT = "lintgate-app/0.1.0";

/** KV TTLs in seconds. */
export const KV_TTL = {
  /** Webhook delivery idempotency key. */
  DELIVERY: 86400, // 24 hours
  /** Installation token cache (tokens valid 1h, cache 50min). */
  TOKEN: 3000, // 50 minutes
  /** PR evaluation state. */
  PR_STATUS: 14400, // 4 hours
  /** Daily triage rate limit counter. */
  TRIAGE_DAILY: 172800, // 48 hours
  /** Branch cleanup audit log. */
  CLEANUP_LOG: 604800, // 7 days
} as const;
