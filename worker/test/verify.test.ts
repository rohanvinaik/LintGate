/**
 * Tests for webhook signature verification.
 */

import { describe, expect, it } from "vitest";
import { verifyWebhookSignature } from "../src/auth/verify";

const SECRET = "test-webhook-secret-123";

async function computeSignature(body: string, secret: string): Promise<string> {
  const encoder = new TextEncoder();
  const key = await crypto.subtle.importKey(
    "raw",
    encoder.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const signature = await crypto.subtle.sign(
    "HMAC",
    key,
    encoder.encode(body),
  );
  const hex = Array.from(new Uint8Array(signature))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
  return `sha256=${hex}`;
}

describe("verifyWebhookSignature", () => {
  it("accepts a valid signature", async () => {
    const body = '{"action":"completed"}';
    const encoder = new TextEncoder();
    const sig = await computeSignature(body, SECRET);

    const result = await verifyWebhookSignature(
      encoder.encode(body).buffer as ArrayBuffer,
      sig,
      SECRET,
    );
    expect(result).toBe(true);
  });

  it("rejects an invalid signature", async () => {
    const body = '{"action":"completed"}';
    const encoder = new TextEncoder();

    const result = await verifyWebhookSignature(
      encoder.encode(body).buffer as ArrayBuffer,
      "sha256=0000000000000000000000000000000000000000000000000000000000000000",
      SECRET,
    );
    expect(result).toBe(false);
  });

  it("rejects a null signature header", async () => {
    const body = '{"action":"completed"}';
    const encoder = new TextEncoder();

    const result = await verifyWebhookSignature(
      encoder.encode(body).buffer as ArrayBuffer,
      null,
      SECRET,
    );
    expect(result).toBe(false);
  });

  it("rejects a signature without sha256= prefix", async () => {
    const body = '{"action":"completed"}';
    const encoder = new TextEncoder();

    const result = await verifyWebhookSignature(
      encoder.encode(body).buffer as ArrayBuffer,
      "invalid-prefix",
      SECRET,
    );
    expect(result).toBe(false);
  });

  it("rejects when body is tampered", async () => {
    const originalBody = '{"action":"completed"}';
    const tamperedBody = '{"action":"malicious"}';
    const encoder = new TextEncoder();
    const sig = await computeSignature(originalBody, SECRET);

    const result = await verifyWebhookSignature(
      encoder.encode(tamperedBody).buffer as ArrayBuffer,
      sig,
      SECRET,
    );
    expect(result).toBe(false);
  });

  it("rejects when secret is wrong", async () => {
    const body = '{"action":"completed"}';
    const encoder = new TextEncoder();
    const sig = await computeSignature(body, SECRET);

    const result = await verifyWebhookSignature(
      encoder.encode(body).buffer as ArrayBuffer,
      sig,
      "wrong-secret",
    );
    expect(result).toBe(false);
  });
});
