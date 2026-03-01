/**
 * Tests for finding classification and issue creation.
 */

import { describe, expect, it } from "vitest";
import { classifyByRule } from "../src/triage/classifier";
import type { Finding } from "../src/util/types";

// ── Rule-Based Classification ───────────────────────────────────────

describe("classifyByRule", () => {
  it("classifies codeql findings as security", () => {
    const finding: Finding = {
      rule: "js/xss",
      message: "Cross-site scripting vulnerability",
      file: "src/handler.ts",
      line: 42,
      severity: "error",
      source: "codeql",
    };
    expect(classifyByRule(finding)).toBe("security");
  });

  it("classifies bandit findings as security", () => {
    const finding: Finding = {
      rule: "B301",
      message: "Use of pickle",
      file: "src/data.py",
      line: 10,
      severity: "warning",
      source: "bandit",
    };
    expect(classifyByRule(finding)).toBe("security");
  });

  it("classifies gitleaks findings as security", () => {
    const finding: Finding = {
      rule: "aws-access-key",
      message: "AWS access key detected",
      file: ".env",
      line: 1,
      severity: "error",
      source: "gitleaks",
    };
    expect(classifyByRule(finding)).toBe("security");
  });

  it("classifies ruff S-rules as security", () => {
    const finding: Finding = {
      rule: "S101",
      message: "Use of assert detected",
      file: "src/auth.py",
      line: 5,
      severity: "warning",
      source: "ruff",
    };
    expect(classifyByRule(finding)).toBe("security");
  });

  it("classifies import sorting as mechanical", () => {
    const finding: Finding = {
      rule: "I001",
      message: "Import block is un-sorted",
      file: "src/main.py",
      line: 1,
      severity: "warning",
      source: "ruff",
    };
    expect(classifyByRule(finding)).toBe("mechanical");
  });

  it("classifies unused imports as mechanical", () => {
    const finding: Finding = {
      rule: "F401",
      message: "'os' imported but unused",
      file: "src/utils.py",
      line: 3,
      severity: "warning",
      source: "ruff",
    };
    expect(classifyByRule(finding)).toBe("mechanical");
  });

  it("classifies pyupgrade as mechanical", () => {
    const finding: Finding = {
      rule: "UP035",
      message: "Use typing.List instead of list",
      file: "src/types.py",
      line: 7,
      severity: "warning",
      source: "ruff",
    };
    expect(classifyByRule(finding)).toBe("mechanical");
  });

  it("classifies unknown rules as structural", () => {
    const finding: Finding = {
      rule: "C901",
      message: "Function is too complex",
      file: "src/handler.py",
      line: 15,
      severity: "warning",
      source: "ruff",
    };
    expect(classifyByRule(finding)).toBe("structural");
  });

  it("classifies unknown sources as structural", () => {
    const finding: Finding = {
      rule: "custom-rule",
      message: "Some custom finding",
      file: "src/app.py",
      line: 1,
      severity: "info",
      source: "custom-linter",
    };
    expect(classifyByRule(finding)).toBe("structural");
  });
});
