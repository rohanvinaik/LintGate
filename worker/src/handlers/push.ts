/**
 * Push event handler — auto-create PR for new branches.
 *
 * When a branch is pushed that doesn't have a PR yet, create one
 * targeting the default branch. Skip protected branches.
 */

import { getInstallationToken } from "../auth/github-app";
import { setAutoMergeStatus } from "../kv/state";
import { approvePR, enableAutoMerge, getPRNodeId } from "../merge/auto-merge";
import { GITHUB_API, PROTECTED_BRANCHES, USER_AGENT } from "../util/constants";
import type { Env, GitHubPR, PushPayload } from "../util/types";

export async function handlePush(
  payload: PushPayload,
  env: Env,
): Promise<Response> {
  const branch = payload.ref.replace("refs/heads/", "");

  // Skip pushes to protected branches
  if (PROTECTED_BRANCHES.has(branch)) {
    return new Response(`Skipped: protected branch '${branch}'`, { status: 200 });
  }

  // Skip tag pushes
  if (payload.ref.startsWith("refs/tags/")) {
    return new Response("Skipped: tag push", { status: 200 });
  }

  // Skip branch deletions (after === 0000...)
  if (payload.after === "0000000000000000000000000000000000000000") {
    return new Response("Skipped: branch deletion", { status: 200 });
  }

  const token = await getInstallationToken(env);
  const { REPO_OWNER: owner, REPO_NAME: repo } = env;

  // Determine base branch
  const baseBranch = payload.repository.default_branch || "main";

  // Don't create PR if pushing to the default branch itself
  if (branch === baseBranch) {
    return new Response("Skipped: push to default branch", { status: 200 });
  }

  // Check if a PR already exists for this branch
  const existingPR = await findPRForBranch(token, owner, repo, branch);
  let pr: GitHubPR;

  if (existingPR) {
    pr = existingPR;
    console.log(`PR #${pr.number} already exists for ${branch}`);
  } else {
    // Create PR
    const title = branchToTitle(branch);
    pr = await createPR(token, owner, repo, {
      title,
      head: branch,
      base: baseBranch,
      body: [
        `Auto-created by \`lintgate[bot]\`.`,
        "",
        "Local gates passed. CI runs as mirror — GitHub branch protection is the remote authority.",
        "",
        "---",
        `Branch: \`${branch}\``,
      ].join("\n"),
    });
    console.log(`Created PR #${pr.number}: ${title}`);
  }

  // Approve PR
  try {
    await approvePR(token, owner, repo, pr.number);
    console.log(`Approved PR #${pr.number}`);
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    console.warn(`PR approval failed (non-fatal): ${msg}`);
  }

  // Enable auto-merge via GraphQL
  try {
    const nodeId = pr.node_id || await getPRNodeId(token, owner, repo, pr.number);
    const enabled = await enableAutoMerge(token, nodeId);
    await setAutoMergeStatus(env.STATE, pr.number, enabled);

    if (enabled) {
      console.log(`Auto-merge enabled for PR #${pr.number}`);
    } else {
      console.log(`Auto-merge not available for PR #${pr.number}, will fall back to direct merge`);
      await setAutoMergeStatus(env.STATE, pr.number, false);
    }
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    console.warn(`enableAutoMerge failed (non-fatal): ${msg}`);
    await setAutoMergeStatus(env.STATE, pr.number, false);
  }

  return new Response(
    existingPR ? `PR #${pr.number} updated` : `PR #${pr.number} created`,
    { status: existingPR ? 200 : 201 },
  );
}

/**
 * Find an open PR with the given head branch.
 */
async function findPRForBranch(
  token: string,
  owner: string,
  repo: string,
  branch: string,
): Promise<GitHubPR | null> {
  const resp = await fetch(
    `${GITHUB_API}/repos/${owner}/${repo}/pulls?state=open&head=${owner}:${branch}&per_page=1`,
    {
      headers: {
        Authorization: `token ${token}`,
        Accept: "application/vnd.github+json",
        "User-Agent": USER_AGENT,
      },
    },
  );

  if (!resp.ok) return null;

  const prs = (await resp.json()) as GitHubPR[];
  return prs.length > 0 ? prs[0]! : null;
}

/**
 * Create a pull request.
 */
async function createPR(
  token: string,
  owner: string,
  repo: string,
  params: { title: string; head: string; base: string; body: string },
): Promise<GitHubPR> {
  const resp = await fetch(
    `${GITHUB_API}/repos/${owner}/${repo}/pulls`,
    {
      method: "POST",
      headers: {
        Authorization: `token ${token}`,
        Accept: "application/vnd.github+json",
        "User-Agent": USER_AGENT,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(params),
    },
  );

  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`Failed to create PR: ${resp.status} ${text}`);
  }

  return (await resp.json()) as GitHubPR;
}

/**
 * Convert a branch name to a PR title.
 *
 * Examples:
 *   "codex/ship-main-fix-auth" → "Fix auth"
 *   "feat/add-user-search"     → "Add user search"
 *   "fix/typo-readme"          → "Typo readme"
 */
function branchToTitle(branch: string): string {
  // Remove common prefixes
  let name = branch
    .replace(/^(codex\/ship-main-|codex\/ship-|codex\/|feat\/|fix\/|quality\/|chore\/|refactor\/)/, "");

  // Replace hyphens/underscores with spaces
  name = name.replace(/[-_]/g, " ").trim();

  // Capitalize first letter
  if (name.length > 0) {
    name = name[0]!.toUpperCase() + name.slice(1);
  }

  return name || branch;
}
