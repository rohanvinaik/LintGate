/**
 * Check suite / check run event handler — thin orchestration.
 *
 * The Worker never parses gate contracts or enumerates individual check names.
 * GitHub branch protection is the remote authority for required checks.
 *
 * On each check_suite.completed or check_run.completed event:
 *   1. Find associated PR
 *   2. Is auto-merge enabled? → GitHub handles merge, just post deduped failure comment if needed
 *   3. No auto-merge (fallback)? → Check combined check-suite status via REST
 *      → all success → squash merge + delete branch
 *      → any failure → post ONE deduped comment
 *      → pending → do nothing
 */

import { getInstallationToken } from "../auth/github-app";
import {
  getAutoMergeStatus,
  hasCommented,
  markCommented,
  setPRStatus,
} from "../kv/state";
import { approvePR, deleteBranch, postPRComment, squashMerge } from "../merge/auto-merge";
import { GITHUB_API, PROTECTED_BRANCHES, SUCCESS_CONCLUSIONS, USER_AGENT } from "../util/constants";
import type { CheckRunPayload, CheckSuitePayload, Env, GitHubPR } from "../util/types";

/**
 * Handle check_suite events (completed).
 */
export async function handleCheckSuite(
  payload: CheckSuitePayload,
  env: Env,
): Promise<Response> {
  // Only act on completed check suites
  if (payload.action !== "completed") {
    return new Response(`Skipped: check_suite.${payload.action}`, { status: 200 });
  }

  const sha = payload.check_suite.head_sha;
  const prs = payload.check_suite.pull_requests;

  if (prs.length === 0) {
    return new Response("No PRs associated with check suite", { status: 200 });
  }

  return processCheckCompletion(sha, prs[0]!, env);
}

/**
 * Handle check_run events (completed).
 */
export async function handleCheckRun(
  payload: CheckRunPayload,
  env: Env,
): Promise<Response> {
  // Only act on completed check runs
  if (payload.action !== "completed") {
    return new Response(`Skipped: check_run.${payload.action}`, { status: 200 });
  }

  const sha = payload.check_run.head_sha;
  const prs = payload.check_run.check_suite.pull_requests;

  if (prs.length === 0) {
    return new Response("No PRs associated with check run", { status: 200 });
  }

  return processCheckCompletion(sha, prs[0]!, env);
}

// ── Check-suite status types ────────────────────────────────────────

type CombinedStatus = "success" | "failure" | "pending";

interface CheckSuiteInfo {
  id: number;
  status: string;
  conclusion: string | null;
}

// ── Core thin orchestration ─────────────────────────────────────────

/**
 * Thin orchestration — replaces the old gate evaluation logic.
 *
 * The Worker doesn't know or care about individual check names.
 * GitHub branch protection handles required checks enforcement.
 */
async function processCheckCompletion(
  sha: string,
  pr: { number: number; head: { ref: string; sha: string }; base: { ref: string } },
  env: Env,
): Promise<Response> {
  const token = await getInstallationToken(env);
  const { REPO_OWNER: owner, REPO_NAME: repo } = env;

  // Skip if PR is for a protected branch
  if (PROTECTED_BRANCHES.has(pr.head.ref)) {
    return new Response("Skipped: protected branch", { status: 200 });
  }

  // Check if auto-merge is enabled for this PR
  const autoMerge = await getAutoMergeStatus(env.STATE, pr.number);

  if (autoMerge === "enabled") {
    // GitHub handles the merge via auto-merge. We only need to post
    // a deduped failure comment if something went wrong.
    const status = await getCombinedCheckSuiteStatus(token, owner, repo, sha);

    if (status === "failure") {
      await postDedupedFailureComment(token, env.STATE, owner, repo, pr.number, sha);
    }

    // Update KV for observability
    await setPRStatus(env.STATE, pr.number, {
      sha,
      lastEval: new Date().toISOString(),
      checksComplete: 0,
      checksTotal: 0,
      allPassed: status === "success",
      merged: false,
    });

    return new Response(
      `PR #${pr.number}: auto-merge enabled, status=${status}`,
      { status: 200 },
    );
  }

  // Fallback path: auto-merge not available, Worker handles merge directly
  const status = await getCombinedCheckSuiteStatus(token, owner, repo, sha);

  if (status === "success") {
    console.log(`All check suites passed for PR #${pr.number}. Merging...`);

    try {
      const prData = await fetchPR(token, owner, repo, pr.number);
      await approvePR(token, owner, repo, pr.number);
      await squashMerge(token, owner, repo, pr.number, prData.title);
      await deleteBranch(token, owner, repo, pr.head.ref);

      await setPRStatus(env.STATE, pr.number, {
        sha,
        lastEval: new Date().toISOString(),
        checksComplete: 0,
        checksTotal: 0,
        allPassed: true,
        merged: true,
      });

      return new Response(`PR #${pr.number} merged`, { status: 200 });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.error(`Merge failed for PR #${pr.number}: ${msg}`);

      await postDedupedFailureComment(
        token, env.STATE, owner, repo, pr.number, sha,
        `## Merge Failed\n\nAll checks passed but merge failed:\n\`\`\`\n${msg}\n\`\`\`\n\nPlease merge manually or push again to re-trigger.`,
      );

      return new Response(`Merge failed: ${msg}`, { status: 500 });
    }
  }

  if (status === "failure") {
    console.log(`Check suites failed for PR #${pr.number}`);
    await postDedupedFailureComment(token, env.STATE, owner, repo, pr.number, sha);

    return new Response(`PR #${pr.number}: checks failed`, { status: 200 });
  }

  // Pending — do nothing
  console.log(`PR #${pr.number}: checks still pending`);
  return new Response(`PR #${pr.number}: pending`, { status: 200 });
}

// ── Combined check-suite status ─────────────────────────────────────

/**
 * Get combined status from all check suites for a commit.
 *
 * Uses the check-suites endpoint (one API call):
 * - If any suite has status != "completed" → pending
 * - If all completed and all conclusions in {success, neutral, skipped} → success
 * - Otherwise → failure
 */
async function getCombinedCheckSuiteStatus(
  token: string,
  owner: string,
  repo: string,
  sha: string,
): Promise<CombinedStatus> {
  const suites = await fetchCheckSuites(token, owner, repo, sha);

  if (suites.length === 0) {
    return "pending";
  }

  for (const suite of suites) {
    if (suite.status !== "completed") {
      return "pending";
    }
  }

  for (const suite of suites) {
    if (!suite.conclusion || !SUCCESS_CONCLUSIONS.has(suite.conclusion)) {
      return "failure";
    }
  }

  return "success";
}

/**
 * Fetch all check suites for a commit SHA.
 */
async function fetchCheckSuites(
  token: string,
  owner: string,
  repo: string,
  sha: string,
): Promise<CheckSuiteInfo[]> {
  const resp = await fetch(
    `${GITHUB_API}/repos/${owner}/${repo}/commits/${sha}/check-suites`,
    {
      headers: {
        Authorization: `token ${token}`,
        Accept: "application/vnd.github+json",
        "User-Agent": USER_AGENT,
      },
    },
  );

  if (!resp.ok) {
    console.warn(`Failed to fetch check suites: ${resp.status}`);
    return [];
  }

  const data = (await resp.json()) as {
    check_suites: Array<{ id: number; status: string; conclusion: string | null }>;
  };

  return data.check_suites.map((s) => ({
    id: s.id,
    status: s.status,
    conclusion: s.conclusion,
  }));
}

// ── Helpers ─────────────────────────────────────────────────────────

/**
 * Post a deduped failure comment on a PR.
 *
 * Keyed on (prNumber, sha) — same SHA completing twice = deduped.
 * New push = new SHA = new comment allowed.
 */
async function postDedupedFailureComment(
  token: string,
  kv: KVNamespace,
  owner: string,
  repo: string,
  prNumber: number,
  sha: string,
  customBody?: string,
): Promise<void> {
  if (await hasCommented(kv, prNumber, sha)) {
    console.log(`Skipping duplicate comment for PR #${prNumber} sha ${sha.slice(0, 7)}`);
    return;
  }

  const body = customBody ?? [
    "## CI Checks Failed",
    "",
    `One or more check suites failed for \`${sha.slice(0, 7)}\`.`,
    "",
    "Fix the failing checks and push again to re-trigger.",
  ].join("\n");

  await postPRComment(token, owner, repo, prNumber, body);
  await markCommented(kv, prNumber, sha);
}

/**
 * Fetch PR details.
 */
async function fetchPR(
  token: string,
  owner: string,
  repo: string,
  prNumber: number,
): Promise<GitHubPR> {
  const resp = await fetch(
    `${GITHUB_API}/repos/${owner}/${repo}/pulls/${prNumber}`,
    {
      headers: {
        Authorization: `token ${token}`,
        Accept: "application/vnd.github+json",
        "User-Agent": USER_AGENT,
      },
    },
  );

  if (!resp.ok) {
    throw new Error(`Failed to fetch PR #${prNumber}: ${resp.status}`);
  }

  return (await resp.json()) as GitHubPR;
}
