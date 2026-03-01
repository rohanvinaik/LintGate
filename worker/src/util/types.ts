/**
 * Shared TypeScript interfaces for the LintGate GitHub App Worker.
 */

// ── Worker Environment ──────────────────────────────────────────────

export interface Env {
  // KV namespace for state management
  STATE: KVNamespace;

  // Secrets (set via `wrangler secret put`)
  GITHUB_APP_ID: string;
  GITHUB_PRIVATE_KEY: string;
  WEBHOOK_SECRET: string;
  GITHUB_TOKEN: string; // PAT for GitHub Models API

  // Vars (set in wrangler.toml)
  REPO_OWNER: string;
  REPO_NAME: string;
}

// ── GitHub API Payloads (subset of fields we use) ───────────────────

export interface PushPayload {
  ref: string;
  after: string;
  before: string;
  repository: {
    full_name: string;
    default_branch: string;
  };
  sender: { login: string };
  head_commit: {
    message: string;
    author: { name: string; email: string };
  } | null;
}

export interface CheckSuitePayload {
  action: string;
  check_suite: {
    id: number;
    head_sha: string;
    status: string;
    conclusion: string | null;
    head_branch: string;
    pull_requests: Array<{
      number: number;
      head: { ref: string; sha: string };
      base: { ref: string };
    }>;
  };
}

export interface CheckRunPayload {
  action: string;
  check_run: {
    id: number;
    name: string;
    head_sha: string;
    status: string;
    conclusion: string | null;
    check_suite: {
      pull_requests: Array<{
        number: number;
        head: { ref: string; sha: string };
        base: { ref: string };
      }>;
    };
  };
}

export interface PullRequestPayload {
  action: string;
  number: number;
  pull_request: {
    number: number;
    title: string;
    state: string;
    merged: boolean;
    head: { ref: string; sha: string };
    base: { ref: string };
    merge_commit_sha: string | null;
  };
}

// ── Finding Triage ──────────────────────────────────────────────────

export type FindingClassification =
  | "mechanical"
  | "structural"
  | "security"
  | "false-positive";

export interface Finding {
  rule: string;
  message: string;
  file: string;
  line: number;
  severity: string;
  source: string; // e.g., "ruff", "codeql", "bandit"
}

// ── GitHub API Response Types ───────────────────────────────────────

export interface GitHubPR {
  number: number;
  node_id: string;
  title: string;
  state: string;
  head: { ref: string; sha: string };
  base: { ref: string };
}
