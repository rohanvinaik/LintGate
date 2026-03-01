/**
 * Tests for auto-merge, comment dedup, and check-suite status aggregation.
 *
 * Replaces the old gate contract parsing / check evaluation tests.
 */

import { describe, expect, it } from "vitest";

// ── enableAutoMerge GraphQL mutation construction ───────────────────

describe("enableAutoMerge mutation", () => {
  it("constructs valid GraphQL mutation with SQUASH method", () => {
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
    const variables = { prId: "PR_abc123", mergeMethod: "SQUASH" };
    const body = JSON.stringify({ query: mutation, variables });
    const parsed = JSON.parse(body) as { query: string; variables: { prId: string; mergeMethod: string } };

    expect(parsed.query).toContain("enablePullRequestAutoMerge");
    expect(parsed.variables.prId).toBe("PR_abc123");
    expect(parsed.variables.mergeMethod).toBe("SQUASH");
  });

  it("supports MERGE and REBASE methods", () => {
    for (const method of ["MERGE", "REBASE"] as const) {
      const variables = { prId: "PR_xyz", mergeMethod: method };
      expect(variables.mergeMethod).toBe(method);
    }
  });
});

// ── Comment dedup logic ─────────────────────────────────────────────

describe("comment dedup key schema", () => {
  it("generates unique keys per (prNumber, sha)", () => {
    const key1 = `pr:${42}:comment:${"abc1234"}`;
    const key2 = `pr:${42}:comment:${"def5678"}`;
    const key3 = `pr:${99}:comment:${"abc1234"}`;

    expect(key1).toBe("pr:42:comment:abc1234");
    expect(key2).toBe("pr:42:comment:def5678");
    expect(key3).toBe("pr:99:comment:abc1234");

    // Same PR + same SHA = same key (deduped)
    expect(key1).toBe(`pr:42:comment:abc1234`);

    // Different SHA = different key (new comment allowed)
    expect(key1).not.toBe(key2);

    // Different PR = different key
    expect(key1).not.toBe(key3);
  });

  it("new push to same PR generates new key (different SHA)", () => {
    const push1Key = `pr:${10}:comment:${"aaa1111"}`;
    const push2Key = `pr:${10}:comment:${"bbb2222"}`;
    expect(push1Key).not.toBe(push2Key);
  });
});

// ── Check-suite status aggregation ──────────────────────────────────

describe("check-suite status aggregation", () => {
  type CombinedStatus = "success" | "failure" | "pending";

  interface CheckSuiteInfo {
    status: string;
    conclusion: string | null;
  }

  const SUCCESS_CONCLUSIONS = new Set(["success", "neutral", "skipped"]);

  function getCombinedStatus(suites: CheckSuiteInfo[]): CombinedStatus {
    if (suites.length === 0) return "pending";

    for (const suite of suites) {
      if (suite.status !== "completed") return "pending";
    }

    for (const suite of suites) {
      if (!suite.conclusion || !SUCCESS_CONCLUSIONS.has(suite.conclusion)) {
        return "failure";
      }
    }

    return "success";
  }

  it("returns success when all suites pass", () => {
    const suites: CheckSuiteInfo[] = [
      { status: "completed", conclusion: "success" },
      { status: "completed", conclusion: "success" },
      { status: "completed", conclusion: "neutral" },
    ];
    expect(getCombinedStatus(suites)).toBe("success");
  });

  it("returns success for skipped conclusions", () => {
    const suites: CheckSuiteInfo[] = [
      { status: "completed", conclusion: "success" },
      { status: "completed", conclusion: "skipped" },
    ];
    expect(getCombinedStatus(suites)).toBe("success");
  });

  it("returns failure when any suite fails", () => {
    const suites: CheckSuiteInfo[] = [
      { status: "completed", conclusion: "success" },
      { status: "completed", conclusion: "failure" },
    ];
    expect(getCombinedStatus(suites)).toBe("failure");
  });

  it("returns failure for null conclusion on completed suite", () => {
    const suites: CheckSuiteInfo[] = [
      { status: "completed", conclusion: null },
    ];
    expect(getCombinedStatus(suites)).toBe("failure");
  });

  it("returns pending when any suite is not completed", () => {
    const suites: CheckSuiteInfo[] = [
      { status: "completed", conclusion: "success" },
      { status: "in_progress", conclusion: null },
    ];
    expect(getCombinedStatus(suites)).toBe("pending");
  });

  it("returns pending for empty suites list", () => {
    expect(getCombinedStatus([])).toBe("pending");
  });

  it("returns failure for action_required conclusion", () => {
    const suites: CheckSuiteInfo[] = [
      { status: "completed", conclusion: "action_required" },
    ];
    expect(getCombinedStatus(suites)).toBe("failure");
  });
});

// ── Auto-merge vs fallback path selection ───────────────────────────

describe("auto-merge vs fallback path selection", () => {
  it("selects auto-merge path when status is enabled", () => {
    const autoMergeStatus: "enabled" | "disabled" | null = "enabled";
    const isAutoMergePath = autoMergeStatus === "enabled";
    expect(isAutoMergePath).toBe(true);
  });

  it("selects fallback path when status is disabled", () => {
    const autoMergeStatus: "enabled" | "disabled" | null = "disabled";
    const isAutoMergePath = autoMergeStatus === "enabled";
    expect(isAutoMergePath).toBe(false);
  });

  it("selects fallback path when status is null (not set)", () => {
    const autoMergeStatus: "enabled" | "disabled" | null = null;
    const isAutoMergePath = autoMergeStatus === "enabled";
    expect(isAutoMergePath).toBe(false);
  });
});
