/**
 * Finding classifier — uses GitHub Models API (GPT-4o-mini free tier)
 * to classify CI findings into actionable categories.
 *
 * Categories:
 * - "mechanical": auto-fixable by ruff/ast-transform
 * - "structural": needs local LLM reasoning
 * - "security": create issue immediately
 * - "false-positive": suppress with documented reason
 *
 * Rate limited to 140 requests/day (safety margin below 150 limit).
 */

import { getTriageCount, incrementTriageCount } from "../kv/state";
import { GITHUB_MODELS_API, MODELS_DAILY_LIMIT } from "../util/constants";
import type { Finding, FindingClassification } from "../util/types";

const CLASSIFICATION_PROMPT = `You are a code quality finding classifier. Classify the finding into exactly one category:

- "mechanical": Can be auto-fixed by a linter/formatter (import sorting, formatting, unused imports, simple type fixes)
- "structural": Requires reasoning about code structure (refactoring, design issues, complex logic errors)
- "security": Security vulnerability (SQL injection, XSS, credential exposure, unsafe deserialization)
- "false-positive": The finding is incorrect or not applicable in this context

Respond with ONLY the category name, nothing else.`;

/**
 * Classify a finding using GitHub Models API.
 *
 * Returns null if rate-limited or API fails — caller should fall back
 * to rule-based classification.
 */
export async function classifyFinding(
  finding: Finding,
  token: string,
  kv: KVNamespace,
): Promise<FindingClassification | null> {
  // Check rate limit
  const count = await getTriageCount(kv);
  if (count >= MODELS_DAILY_LIMIT) {
    console.log("Triage rate limit reached, falling back to rule-based");
    return classifyByRule(finding);
  }

  try {
    const resp = await fetch(GITHUB_MODELS_API, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        model: "gpt-4o-mini",
        messages: [
          { role: "system", content: CLASSIFICATION_PROMPT },
          {
            role: "user",
            content: JSON.stringify({
              rule: finding.rule,
              message: finding.message,
              file: finding.file,
              severity: finding.severity,
              source: finding.source,
            }),
          },
        ],
        max_tokens: 20,
        temperature: 0,
      }),
    });

    if (!resp.ok) {
      console.warn(`GitHub Models API error: ${resp.status}`);
      return classifyByRule(finding);
    }

    // Increment rate limit counter
    await incrementTriageCount(kv);

    const data = (await resp.json()) as {
      choices: Array<{ message: { content: string } }>;
    };

    const content = data.choices[0]?.message.content.trim().toLowerCase();
    return parseClassification(content);
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    console.warn(`Classification failed: ${msg}`);
    return classifyByRule(finding);
  }
}

/**
 * Parse an LLM response into a valid classification.
 */
function parseClassification(content: string | undefined): FindingClassification {
  if (!content) return "structural";

  const normalized = content.replace(/[^a-z-]/g, "");

  if (normalized === "mechanical") return "mechanical";
  if (normalized === "structural") return "structural";
  if (normalized === "security") return "security";
  if (normalized === "false-positive" || normalized === "falsepositive") return "false-positive";

  // Default to structural (needs human attention)
  return "structural";
}

/**
 * Rule-based classification fallback when LLM is unavailable or rate-limited.
 *
 * Uses simple heuristics based on the finding source and rule.
 */
export function classifyByRule(finding: Finding): FindingClassification {
  const rule = finding.rule.toLowerCase();
  const source = finding.source.toLowerCase();

  // Security sources
  if (["codeql", "bandit", "trivy", "trufflehog", "gitleaks"].includes(source)) {
    return "security";
  }

  // Security rules from other sources
  if (rule.startsWith("s") && source === "ruff") {
    // S*** rules in ruff are bandit-derived security checks
    return "security";
  }

  // Mechanical rules (auto-fixable)
  const mechanicalPrefixes = [
    "e1", "e2", "e3", "e4", "e5", "e7", "w", // pycodestyle
    "i", // isort
    "f401", "f811", // unused imports/variables
    "up", // pyupgrade
    "c4", // flake8-comprehensions
  ];
  if (mechanicalPrefixes.some((p) => rule.toLowerCase().startsWith(p))) {
    return "mechanical";
  }

  // Everything else needs reasoning
  return "structural";
}
