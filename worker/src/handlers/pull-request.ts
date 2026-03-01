/**
 * Pull request event handler — post-merge branch cleanup.
 *
 * When a PR is merged, delete the head branch if it's not protected.
 * This is a belt-and-suspenders approach — the merge API may already
 * delete the branch, but this ensures cleanup on all merge paths.
 */

import { getInstallationToken } from "../auth/github-app";
import { clearPRStatus } from "../kv/state";
import { deleteBranch } from "../merge/auto-merge";
import { PROTECTED_BRANCHES } from "../util/constants";
import type { Env, PullRequestPayload } from "../util/types";

export async function handlePullRequest(
  payload: PullRequestPayload,
  env: Env,
): Promise<Response> {
  // Only act on merged PRs
  if (payload.action !== "closed" || !payload.pull_request.merged) {
    return new Response(`Skipped: pull_request.${payload.action}`, { status: 200 });
  }

  const headRef = payload.pull_request.head.ref;
  const prNumber = payload.pull_request.number;

  // Clean up KV state for this PR
  await clearPRStatus(env.STATE, prNumber);

  // Don't delete protected branches
  if (PROTECTED_BRANCHES.has(headRef)) {
    return new Response(`Skipped: protected branch '${headRef}'`, { status: 200 });
  }

  // Delete the head branch
  try {
    const token = await getInstallationToken(env);
    const { REPO_OWNER: owner, REPO_NAME: repo } = env;
    await deleteBranch(token, owner, repo, headRef);
    console.log(`Deleted branch '${headRef}' after PR #${prNumber} merge`);
    return new Response(`Branch '${headRef}' deleted`, { status: 200 });
  } catch (err) {
    // Branch may already be deleted — not an error
    const msg = err instanceof Error ? err.message : String(err);
    console.warn(`Branch deletion for '${headRef}' failed (may already be gone): ${msg}`);
    return new Response(`Branch cleanup attempted: ${msg}`, { status: 200 });
  }
}
