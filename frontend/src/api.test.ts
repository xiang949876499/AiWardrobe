import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  createGarmentUploadTask,
  fetchAnalysisTask,
  stableIdempotencyKey,
  waitForAnalysisTask
} from "./api";
import type { AnalysisTask } from "./types";

const task = (status: AnalysisTask["status"]): AnalysisTask => ({
  id: "task-1",
  kind: "garment_single",
  status,
  stage: status === "succeeded" ? "persisting" : "recognizing",
  submission_status: status === "succeeded" ? "completed" : "not_submitted",
  cancel_requested: false,
  action_required: false,
  attempt_count: 1,
  error: null,
  result_refs: [],
  poll_after_seconds: status === "succeeded" ? null : 1,
  created_at: "2026-10-01T00:00:00Z",
  updated_at: "2026-10-01T00:00:01Z"
});

afterEach(() => vi.restoreAllMocks());

describe("v2 task API", () => {
  it("returns a paused unknown submission for explicit reconciliation without continued polling", async () => {
    const paused = { ...task("running"), stage: "awaiting_resolution", submission_status: "unknown", action_required: true };
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify(paused)));
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), 1200);
    try {
      await expect(waitForAnalysisTask("token", "task-1", { signal: controller.signal })).resolves.toMatchObject(paused);
      expect(fetchMock).toHaveBeenCalledTimes(1);
    } finally { window.clearTimeout(timer); }
  });
  it("reuses a persisted idempotency key without storing the bearer token", async () => {
    const key1 = await stableIdempotencyKey("header.payload.signature", "garment-single", "sha256-a");
    const key2 = await stableIdempotencyKey("header.payload.signature", "garment-single", "sha256-a");
    const otherOperation = await stableIdempotencyKey("header.payload.signature", "purchase-image", "sha256-a");
    expect(key2).toBe(key1);
    expect(otherOperation).not.toBe(key1);
    expect(Object.keys(localStorage).some((key) => key.includes("payload") || key.includes("signature"))).toBe(false);
  });

  it("sends multipart tasks with Idempotency-Key and bearer auth", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify(task("queued")), { status: 202 }));
    const file = new File(["image"], "shirt.jpg", { type: "image/jpeg" });
    await createGarmentUploadTask("token-1", file, "single", "stable-key");
    const [url, options] = fetchMock.mock.calls[0];
    const headers = new Headers(options?.headers);
    expect(url).toBe("/v2/tasks/garment-upload");
    expect(headers.get("Authorization")).toBe("Bearer token-1");
    expect(headers.get("Idempotency-Key")).toBe("stable-key");
    expect(options?.body).toBeInstanceOf(FormData);
  });

  it("preserves structured server error codes and HTTP status", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ detail: { code: "idempotency_conflict" } }), { status: 409 }));
    await expect(fetchAnalysisTask("token-1", "task-1")).rejects.toMatchObject({
      status: 409,
      code: "idempotency_conflict"
    });
  });

  it("polls an in-flight task until it reaches a terminal state", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response(JSON.stringify(task("running")), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(task("succeeded")), { status: 200 }));
    const updates: string[] = [];
    const complete = await waitForAnalysisTask("token-1", "task-1", {
      intervalMs: 0,
      onUpdate: (value) => updates.push(value.status)
    });
    expect(complete.status).toBe("succeeded");
    expect(updates).toEqual(["running", "succeeded"]);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
