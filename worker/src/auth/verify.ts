/**
 * Webhook signature verification using HMAC SHA-256.
 *
 * Verifies the `x-hub-signature-256` header against the raw request body
 * using the shared webhook secret. Uses Web Crypto API (crypto.subtle)
 * — no external dependencies.
 */

/**
 * Verify a GitHub webhook signature.
 *
 * @param body - Raw request body as ArrayBuffer
 * @param signatureHeader - Value of x-hub-signature-256 header (e.g., "sha256=abc123...")
 * @param secret - Webhook secret string
 * @returns true if signature is valid
 */
export async function verifyWebhookSignature(
  body: ArrayBuffer,
  signatureHeader: string | null,
  secret: string,
): Promise<boolean> {
  if (!signatureHeader) return false;

  // Header format: "sha256=<hex-digest>"
  const prefix = "sha256=";
  if (!signatureHeader.startsWith(prefix)) return false;

  const receivedHex = signatureHeader.slice(prefix.length);

  // Import the secret as an HMAC key
  const encoder = new TextEncoder();
  const key = await crypto.subtle.importKey(
    "raw",
    encoder.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );

  // Compute HMAC of the body
  const signature = await crypto.subtle.sign("HMAC", key, body);

  // Convert to hex string
  const computedHex = arrayBufferToHex(signature);

  // Constant-time comparison to prevent timing attacks
  return timingSafeEqual(computedHex, receivedHex);
}

/** Convert ArrayBuffer to lowercase hex string. */
function arrayBufferToHex(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  const hexParts: string[] = [];
  for (const byte of bytes) {
    hexParts.push(byte.toString(16).padStart(2, "0"));
  }
  return hexParts.join("");
}

/** Constant-time string comparison. */
function timingSafeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;

  let result = 0;
  for (let i = 0; i < a.length; i++) {
    result |= a.charCodeAt(i) ^ b.charCodeAt(i);
  }
  return result === 0;
}
