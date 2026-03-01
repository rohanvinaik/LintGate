/**
 * Scheduled handler — weekly stale branch cleanup (cron trigger).
 *
 * Runs every Monday at 07:00 UTC. Deletes merged remote branches
 * older than stale_branch_days. Protects configured branches.
 * Logs results to KV for auditing.
 */

import { getInstallationToken } from "../auth/github-app";
import { logCleanup, type CleanupLog } from "../kv/state";
import { deleteBranch } from "../merge/auto-merge";
import { GITHUB_API, PROTECTED_BRANCHES, STALE_BRANCH_DAYS, USER_AGENT } from "../util/constants";
import type { Env } from "../util/types";

interface BranchInfo {
  name: string;
  commit: {
    sha: string;
    url: string;
  };
  protected: boolean;
}

/**
 * Handle the weekly cron trigger for stale branch cleanup.
 */
export async function handleSchedule(env: Env): Promise<void> {
  const token = await getInstallationToken(env);
  const { REPO_OWNER: owner, REPO_NAME: repo } = env;

  // Use constants directly — no contract parsing needed
  const protectedSet = new Set([...PROTECTED_BRANCHES]);
  const staleDays = STALE_BRANCH_DAYS;

  // Fetch all branches
  const branches = await listBranches(token, owner, repo);
  const now = Date.now();
  const staleThresholdMs = staleDays * 24 * 60 * 60 * 1000;

  const deleted: string[] = [];
  const skipped: string[] = [];

  for (const branch of branches) {
    // Skip protected branches
    if (protectedSet.has(branch.name) || branch.protected) {
      continue;
    }

    // Get the commit date to determine age
    const commitDate = await getCommitDate(token, owner, repo, branch.commit.sha);
    if (!commitDate) continue;

    const ageMs = now - commitDate.getTime();

    if (ageMs > staleThresholdMs) {
      // Check if branch is merged into main
      const merged = await isBranchMerged(token, owner, repo, branch.name);

      if (merged) {
        try {
          await deleteBranch(token, owner, repo, branch.name);
          deleted.push(branch.name);
          console.log(`Deleted stale merged branch: ${branch.name}`);
        } catch (err) {
          const msg = err instanceof Error ? err.message : String(err);
          console.warn(`Failed to delete branch ${branch.name}: ${msg}`);
          skipped.push(branch.name);
        }
      } else {
        // Stale but unmerged — log but don't delete
        skipped.push(branch.name);
        console.log(`Skipped stale unmerged branch: ${branch.name}`);
      }
    }
  }

  // Log cleanup results
  const log: CleanupLog = {
    deleted,
    skipped,
    timestamp: new Date().toISOString(),
  };
  await logCleanup(env.STATE, log);

  console.log(`Branch cleanup complete: ${deleted.length} deleted, ${skipped.length} skipped`);
}

/**
 * List all branches in the repository.
 */
async function listBranches(
  token: string,
  owner: string,
  repo: string,
): Promise<BranchInfo[]> {
  const allBranches: BranchInfo[] = [];
  let page = 1;

  while (true) {
    const resp = await fetch(
      `${GITHUB_API}/repos/${owner}/${repo}/branches?per_page=100&page=${page}`,
      {
        headers: {
          Authorization: `token ${token}`,
          Accept: "application/vnd.github+json",
          "User-Agent": USER_AGENT,
        },
      },
    );

    if (!resp.ok) break;

    const branches = (await resp.json()) as BranchInfo[];
    if (branches.length === 0) break;

    allBranches.push(...branches);
    page++;

    // Safety cap
    if (page > 20) break;
  }

  return allBranches;
}

/**
 * Get the committer date for a specific commit.
 */
async function getCommitDate(
  token: string,
  owner: string,
  repo: string,
  sha: string,
): Promise<Date | null> {
  const resp = await fetch(
    `${GITHUB_API}/repos/${owner}/${repo}/commits/${sha}`,
    {
      headers: {
        Authorization: `token ${token}`,
        Accept: "application/vnd.github+json",
        "User-Agent": USER_AGENT,
      },
    },
  );

  if (!resp.ok) return null;

  const data = (await resp.json()) as {
    commit: { committer: { date: string } };
  };
  return new Date(data.commit.committer.date);
}

/**
 * Check if a branch has been merged into the default branch.
 */
async function isBranchMerged(
  token: string,
  owner: string,
  repo: string,
  branch: string,
): Promise<boolean> {
  // Use the compare API: if the branch is behind main with 0 ahead commits,
  // it's been merged
  const resp = await fetch(
    `${GITHUB_API}/repos/${owner}/${repo}/compare/main...${branch}`,
    {
      headers: {
        Authorization: `token ${token}`,
        Accept: "application/vnd.github+json",
        "User-Agent": USER_AGENT,
      },
    },
  );

  if (!resp.ok) return false;

  const data = (await resp.json()) as { status: string; ahead_by: number };
  // "behind" or "identical" means fully merged
  return data.status === "behind" || data.status === "identical" || data.ahead_by === 0;
}
