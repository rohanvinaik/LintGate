/**
 * Event router — dispatch webhook events to the appropriate handler.
 */

import { handleCheckRun, handleCheckSuite } from "./handlers/check-suite";
import { handlePullRequest } from "./handlers/pull-request";
import { handlePush } from "./handlers/push";
import type {
  CheckRunPayload,
  CheckSuitePayload,
  Env,
  PullRequestPayload,
  PushPayload,
} from "./util/types";

/**
 * Route a GitHub webhook event to its handler.
 *
 * @param event - The x-github-event header value
 * @param payload - Parsed JSON payload
 * @param env - Worker environment bindings
 */
export async function router(
  event: string | null,
  payload: unknown,
  env: Env,
): Promise<Response> {
  switch (event) {
    case "push":
      return handlePush(payload as PushPayload, env);

    case "check_suite":
      return handleCheckSuite(payload as CheckSuitePayload, env);

    case "check_run":
      return handleCheckRun(payload as CheckRunPayload, env);

    case "pull_request":
      return handlePullRequest(payload as PullRequestPayload, env);

    case "ping":
      return new Response("pong", { status: 200 });

    default:
      console.log(`Unhandled event: ${event}`);
      return new Response(`Unhandled event: ${event}`, { status: 200 });
  }
}
