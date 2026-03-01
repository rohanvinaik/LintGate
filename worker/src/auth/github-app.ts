/**
 * GitHub App authentication — JWT generation and installation token exchange.
 *
 * Uses Web Crypto API for RS256 JWT signing. No external JWT libraries.
 *
 * Flow:
 * 1. Generate short-lived JWT (10 min) signed with App's private key
 * 2. Exchange JWT for installation access token (1 hour validity)
 * 3. Cache installation token in KV (50 min TTL)
 */

import { GITHUB_API, KV_TTL, USER_AGENT } from "../util/constants";
import type { Env } from "../util/types";

/**
 * Get a valid installation token, using KV cache when available.
 */
export async function getInstallationToken(env: Env): Promise<string> {
  // 1. Check KV cache
  const cached = await env.STATE.get("token:installation");
  if (cached) return cached;

  // 2. Generate App JWT
  const jwt = await generateAppJWT(env.GITHUB_APP_ID, env.GITHUB_PRIVATE_KEY);

  // 3. Find installation ID for our repo
  const installationId = await getInstallationId(jwt, env.REPO_OWNER, env.REPO_NAME);

  // 4. Exchange for installation token
  const resp = await fetch(
    `${GITHUB_API}/app/installations/${installationId}/access_tokens`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${jwt}`,
        Accept: "application/vnd.github+json",
        "User-Agent": USER_AGENT,
      },
    },
  );

  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`Failed to get installation token: ${resp.status} ${text}`);
  }

  const data = (await resp.json()) as { token: string; expires_at: string };

  // 5. Cache in KV
  await env.STATE.put("token:installation", data.token, {
    expirationTtl: KV_TTL.TOKEN,
  });

  return data.token;
}

/**
 * Find the installation ID for a specific repository.
 */
async function getInstallationId(
  jwt: string,
  owner: string,
  repo: string,
): Promise<number> {
  const resp = await fetch(`${GITHUB_API}/repos/${owner}/${repo}/installation`, {
    headers: {
      Authorization: `Bearer ${jwt}`,
      Accept: "application/vnd.github+json",
      "User-Agent": USER_AGENT,
    },
  });

  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`Failed to get installation: ${resp.status} ${text}`);
  }

  const data = (await resp.json()) as { id: number };
  return data.id;
}

/**
 * Generate a JWT for GitHub App authentication (RS256, 10-min expiry).
 *
 * Uses Web Crypto API — no external dependencies.
 */
async function generateAppJWT(appId: string, privateKeyPem: string): Promise<string> {
  const now = Math.floor(Date.now() / 1000);

  const header = {
    alg: "RS256",
    typ: "JWT",
  };

  const payload = {
    iat: now - 60, // Issued 60s ago (clock skew tolerance)
    exp: now + 600, // Expires in 10 minutes
    iss: appId,
  };

  const headerB64 = base64urlEncode(JSON.stringify(header));
  const payloadB64 = base64urlEncode(JSON.stringify(payload));
  const signingInput = `${headerB64}.${payloadB64}`;

  // Import PEM private key
  const key = await importPemPrivateKey(privateKeyPem);

  // Sign with RS256
  const encoder = new TextEncoder();
  const signature = await crypto.subtle.sign(
    "RSASSA-PKCS1-v1_5",
    key,
    encoder.encode(signingInput),
  );

  const signatureB64 = base64urlEncodeBuffer(signature);

  return `${signingInput}.${signatureB64}`;
}

/**
 * Import a PEM-encoded RSA private key for use with Web Crypto API.
 */
async function importPemPrivateKey(pem: string): Promise<CryptoKey> {
  // Strip PEM headers and whitespace
  const pemBody = pem
    .replace(/-----BEGIN RSA PRIVATE KEY-----/g, "")
    .replace(/-----END RSA PRIVATE KEY-----/g, "")
    .replace(/-----BEGIN PRIVATE KEY-----/g, "")
    .replace(/-----END PRIVATE KEY-----/g, "")
    .replace(/\s/g, "");

  // Decode base64 to binary
  const binaryString = atob(pemBody);
  const bytes = new Uint8Array(binaryString.length);
  for (let i = 0; i < binaryString.length; i++) {
    bytes[i] = binaryString.charCodeAt(i);
  }

  // Determine key format — PKCS#1 keys need different handling
  // GitHub App keys are typically PKCS#1 (BEGIN RSA PRIVATE KEY)
  // Web Crypto expects PKCS#8 (BEGIN PRIVATE KEY)
  const format = pem.includes("BEGIN RSA PRIVATE KEY") ? "pkcs1" : "pkcs8";

  if (format === "pkcs1") {
    // Wrap PKCS#1 key in PKCS#8 envelope
    const pkcs8 = wrapPkcs1InPkcs8(bytes);
    return crypto.subtle.importKey(
      "pkcs8",
      pkcs8,
      { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" },
      false,
      ["sign"],
    );
  }

  return crypto.subtle.importKey(
    "pkcs8",
    bytes.buffer,
    { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" },
    false,
    ["sign"],
  );
}

/**
 * Wrap a PKCS#1 RSA private key in a PKCS#8 envelope.
 *
 * PKCS#8 = SEQUENCE { version, algorithmIdentifier, privateKey }
 * where privateKey is the PKCS#1 key wrapped in an OCTET STRING.
 */
function wrapPkcs1InPkcs8(pkcs1Key: Uint8Array): ArrayBuffer {
  // RSA OID: 1.2.840.113549.1.1.1
  const rsaOid = new Uint8Array([
    0x30, 0x0d, // SEQUENCE (13 bytes)
    0x06, 0x09, // OID (9 bytes)
    0x2a, 0x86, 0x48, 0x86, 0xf7, 0x0d, 0x01, 0x01, 0x01,
    0x05, 0x00, // NULL
  ]);

  // Version INTEGER 0
  const version = new Uint8Array([0x02, 0x01, 0x00]);

  // OCTET STRING wrapping the PKCS#1 key
  const octetStringHeader = encodeLength(pkcs1Key.length);
  const octetString = new Uint8Array(1 + octetStringHeader.length + pkcs1Key.length);
  octetString[0] = 0x04; // OCTET STRING tag
  octetString.set(octetStringHeader, 1);
  octetString.set(pkcs1Key, 1 + octetStringHeader.length);

  // Outer SEQUENCE
  const innerLength = version.length + rsaOid.length + octetString.length;
  const seqHeader = encodeLength(innerLength);
  const result = new Uint8Array(1 + seqHeader.length + innerLength);
  result[0] = 0x30; // SEQUENCE tag
  result.set(seqHeader, 1);
  let offset = 1 + seqHeader.length;
  result.set(version, offset);
  offset += version.length;
  result.set(rsaOid, offset);
  offset += rsaOid.length;
  result.set(octetString, offset);

  return result.buffer;
}

/** Encode ASN.1 DER length. */
function encodeLength(length: number): Uint8Array {
  if (length < 0x80) {
    return new Uint8Array([length]);
  }
  if (length < 0x100) {
    return new Uint8Array([0x81, length]);
  }
  return new Uint8Array([0x82, (length >> 8) & 0xff, length & 0xff]);
}

/** Base64url encode a string. */
function base64urlEncode(str: string): string {
  const encoder = new TextEncoder();
  return base64urlEncodeBuffer(encoder.encode(str).buffer as ArrayBuffer);
}

/** Base64url encode an ArrayBuffer. */
function base64urlEncodeBuffer(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  for (const byte of bytes) {
    binary += String.fromCharCode(byte);
  }
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}
