/**
 * Auto-merge — approve PR and squash merge via GitHub API.
 *
 * GitHub's merge API signs commits made by GitHub Apps with GitHub's key,
 * producing a "Verified" badge automatically. No custom GPG key needed.
 */

import { GITHUB_API, USER_AGENT } from "../util/constants";

/**
 * Create a PR review approval as the App.
 */
export async function approvePR(
  token: string,
  owner: string,
  repo: string,
  prNumber: number,
): Promise<void> {
  const resp = await fetch(
    `${GITHUB_API}/repos/${owner}/${repo}/pulls/${prNumber}/reviews`,
    {
      method: "POST",
      headers: {
        Authorization: `token ${token}`,
        Accept: "application/vnd.github+json",
        "User-Agent": USER_AGENT,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        event: "APPROVE",
        body: "All required checks passed. Gate contract satisfied. \u2705",
      }),
    },
  );

  if (!resp.ok) {
    const text = await resp.text();
    // 422 often means "already approved" or review restrictions — not fatal
    if (resp.status !== 422) {
      throw new Error(`Failed to approve PR: ${resp.status} ${text}`);
    }
    console.warn(`PR approval returned 422 (may be already approved): ${text}`);
  }
}

/**
 * Squash merge a PR.
 *
 * Uses GitHub's merge API which signs the commit with GitHub's key.
 */
export async function squashMerge(
  token: string,
  owner: string,
  repo: string,
  prNumber: number,
  prTitle: string,
): Promise<void> {
  const resp = await fetch(
    `${GITHUB_API}/repos/${owner}/${repo}/pulls/${prNumber}/merge`,
    {
      method: "PUT",
      headers: {
        Authorization: `token ${token}`,
        Accept: "application/vnd.github+json",
        "User-Agent": USER_AGENT,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        merge_method: "squash",
        commit_title: `${prTitle} (#${prNumber})`,
      }),
    },
  );

  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`Failed to merge PR #${prNumber}: ${resp.status} ${text}`);
  }

  console.log(`Merged PR #${prNumber} via squash`);
}

/**
 * Delete a branch after merge.
 */
export async function deleteBranch(
  token: string,
  owner: string,
  repo: string,
  branch: string,
): Promise<void> {
  const resp = await fetch(
    `${GITHUB_API}/repos/${owner}/${repo}/git/refs/heads/${branch}`,
    {
      method: "DELETE",
      headers: {
        Authorization: `token ${token}`,
        Accept: "application/vnd.github+json",
        "User-Agent": USER_AGENT,
      },
    },
  );

  if (!resp.ok && resp.status !== 422) {
    // 422 = branch already deleted — not an error
    const text = await resp.text();
    console.warn(`Failed to delete branch ${branch}: ${resp.status} ${text}`);
  }
}

/**
 * Close a PR without merging (used by stale branch cleanup).
 */
export async function closePR(
  token: string,
  owner: string,
  repo: string,
  prNumber: number,
  comment: string,
): Promise<void> {
  // Post a comment explaining the closure
  await postPRComment(token, owner, repo, prNumber, comment);

  const resp = await fetch(
    `${GITHUB_API}/repos/${owner}/${repo}/pulls/${prNumber}`,
    {
      method: "PATCH",
      headers: {
        Authorization: `token ${token}`,
        Accept: "application/vnd.github+json",
        "User-Agent": USER_AGENT,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ state: "closed" }),
    },
  );

  if (!resp.ok) {
    const text = await resp.text();
    console.warn(`Failed to close PR #${prNumber}: ${resp.status} ${text}`);
  }
}

/**
 * Get the GraphQL node_id for a PR (needed for enableAutoMerge mutation).
 */
export async function getPRNodeId(
  token: string,
  owner: string,
  repo: string,
  prNumber: number,
): Promise<string> {
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

  const data = (await resp.json()) as { node_id: string };
  return data.node_id;
}

/**
 * Enable auto-merge on a PR via GitHub GraphQL API.
 *
 * Requires the repo to have "Allow auto-merge" enabled in settings.
 * Falls back gracefully — caller should catch errors and use direct merge.
 */
export async function enableAutoMerge(
  token: string,
  prNodeId: string,
  mergeMethod: "SQUASH" | "MERGE" | "REBASE" = "SQUASH",
): Promise<boolean> {
  const mutation = `
    mutation EnableAutoMerge($prId: ID!, $mergeMethod: PullRequestMergeMethod!) {
      enablePullRequestAutoMerge(input: {
        pullRequestId: $prId,
        mergeMethod: $mergeMethod
      }) {
        pullRequest { number }
      }
    }
  `;

  const resp = await fetch("https://api.github.com/graphql", {
    method: "POST",
    headers: {
      Authorization: `bearer ${token}`,
      "Content-Type": "application/json",
      "User-Agent": USER_AGENT,
    },
    body: JSON.stringify({
      query: mutation,
      variables: { prId: prNodeId, mergeMethod },
    }),
  });

  if (!resp.ok) {
    const text = await resp.text();
    console.warn(`enableAutoMerge HTTP error: ${resp.status} ${text}`);
    return false;
  }

  const data = (await resp.json()) as { errors?: Array<{ message: string }> };
  if (data.errors && data.errors.length > 0) {
    console.warn(`enableAutoMerge GraphQL error: ${data.errors[0]!.message}`);
    return false;
  }

  return true;
}

/**
 * Post a comment on a PR (used for failure reports).
 */
export async function postPRComment(
  token: string,
  owner: string,
  repo: string,
  prNumber: number,
  body: string,
): Promise<void> {
  const resp = await fetch(
    `${GITHUB_API}/repos/${owner}/${repo}/issues/${prNumber}/comments`,
    {
      method: "POST",
      headers: {
        Authorization: `token ${token}`,
        Accept: "application/vnd.github+json",
        "User-Agent": USER_AGENT,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ body }),
    },
  );

  if (!resp.ok) {
    const text = await resp.text();
    console.warn(`Failed to post PR comment: ${resp.status} ${text}`);
  }
}
