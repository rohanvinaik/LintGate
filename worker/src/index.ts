/**
 * LintGate GitHub App — Cloudflare Worker entry point.
 *
 * Receives GitHub webhook events and orchestrates:
 * - PR creation on push
 * - Gate contract evaluation on check completion
 * - Auto-merge when all checks pass
 * - Branch cleanup on merge
 * - Stale branch cleanup on weekly schedule
 *
 * Replaces the poll-based ship_main.py pipeline.
 */

import { verifyWebhookSignature } from "./auth/verify";
import { handleSchedule } from "./handlers/schedule";
import { isDuplicate } from "./kv/state";
import { router } from "./router";
import type { Env } from "./util/types";

export default {
  /**
   * Handle incoming HTTP requests (webhook events).
   */
  async fetch(request: Request, env: Env): Promise<Response> {
    // Only accept POST requests
    if (request.method !== "POST") {
      return new Response("Method not allowed", { status: 405 });
    }

    // Read body as ArrayBuffer for signature verification
    const body = await request.arrayBuffer();

    // 1. Verify webhook signature (HMAC SHA-256)
    const signatureHeader = request.headers.get("x-hub-signature-256");
    const isValid = await verifyWebhookSignature(body, signatureHeader, env.WEBHOOK_SECRET);
    if (!isValid) {
      console.warn("Invalid webhook signature");
      return new Response("Invalid signature", { status: 401 });
    }

    // 2. Parse event type and delivery ID
    const event = request.headers.get("x-github-event");
    const deliveryId = request.headers.get("x-github-delivery");

    if (!event) {
      return new Response("Missing x-github-event header", { status: 400 });
    }

    // 3. Idempotency check — prevent double-processing
    if (await isDuplicate(env.STATE, deliveryId)) {
      return new Response("Already processed", { status: 200 });
    }

    // 4. Parse payload
    let payload: unknown;
    try {
      const decoder = new TextDecoder();
      payload = JSON.parse(decoder.decode(body));
    } catch {
      return new Response("Invalid JSON payload", { status: 400 });
    }

    // 5. Route to handler
    try {
      return await router(event, payload, env);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.error(`Handler error for ${event}: ${msg}`);
      return new Response(`Internal error: ${msg}`, { status: 500 });
    }
  },

  /**
   * Handle scheduled events (cron triggers).
   *
   * Configured in wrangler.toml: "0 7 * * 1" (Monday 07:00 UTC)
   */
  async scheduled(_event: ScheduledEvent, env: Env): Promise<void> {
    try {
      await handleSchedule(env);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.error(`Scheduled handler error: ${msg}`);
    }
  },
};

// Re-export Env type for wrangler type generation
export type { Env };
