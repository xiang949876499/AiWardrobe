import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import * as api from "./api";
import type { AnalysisTask, WeddingStoryDraft } from "./types";

const reply = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });
const task = (status: AnalysisTask["status"], extra = {}) => ({
  id: "task/one", kind: "purchase_url", status, stage: "recognizing",
  submission_status: "submitted", cancel_requested: false, action_required: false,
  attempt_count: 1, result_refs: [], error: null, poll_after_seconds: 0, ...extra,
});

beforeEach(() => { localStorage.clear(); vi.stubGlobal("fetch", vi.fn()); });
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); });

describe("task recovery and account partitioning", () => {
  test("an already aborted view never starts a task lookup", async () => {
    const controller = new AbortController(); controller.abort();
    await expect(api.waitForAnalysisTask("owner", "task/one", { signal: controller.signal })).rejects.toMatchObject({ name: "AbortError" });
    expect(fetch).not.toHaveBeenCalled();
  });

  test("leaving a running task aborts its timer without another lookup", async () => {
    vi.useFakeTimers();
    vi.mocked(fetch).mockResolvedValue(reply(task("running")));
    const controller = new AbortController();
    const waiting = api.waitForAnalysisTask("owner", "task/one", { signal: controller.signal });
    const rejected = expect(waiting).rejects.toMatchObject({ name: "AbortError" });
    await vi.advanceTimersByTimeAsync(0);
    controller.abort();
    await rejected;
    await vi.advanceTimersByTimeAsync(30000);
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  test("zero provider poll hints are bounded and a failed result is not resubmitted", async () => {
    vi.useFakeTimers();
    vi.mocked(fetch).mockResolvedValueOnce(reply(task("running"))).mockResolvedValueOnce(reply(task("failed", { error: { code: "provider_request_failed", retryable: true } })));
    const waiting = api.waitForAnalysisTask("owner", "task/one");
    await vi.advanceTimersByTimeAsync(499);
    expect(fetch).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    await expect(waiting).resolves.toMatchObject({ status: "failed" });
    expect(vi.mocked(fetch).mock.calls.every(([, options]) => !options?.method)).toBe(true);
  });

  test("new account, expired key and explicit clearing cannot reuse another reservation", async () => {
    const original = await api.stableIdempotencyKey("owner-a", "purchase", "image");
    expect(await api.stableIdempotencyKey("owner-b", "purchase", "image")).not.toBe(original);
    const now = Date.now(); vi.spyOn(Date, "now").mockReturnValue(now + 86400001);
    const renewed = await api.stableIdempotencyKey("owner-a", "purchase", "image");
    expect(renewed).not.toBe(original);
    await api.clearIdempotencyKey("owner-a", "purchase", "image");
    expect(await api.stableIdempotencyKey("owner-a", "purchase", "image")).not.toBe(renewed);
    expect(JSON.stringify(localStorage)).not.toMatch(/owner-a|owner-b/);
  });

  test("corrupted or unavailable optional storage does not block submission", async () => {
    const first = await api.stableIdempotencyKey("owner", "purchase", "image");
    localStorage.setItem(Object.keys(localStorage)[0], "invalid json");
    expect(await api.stableIdempotencyKey("owner", "purchase", "image")).not.toBe(first);
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new DOMException("quota", "QuotaExceededError"); });
    await expect(api.stableIdempotencyKey("other-owner", "purchase", "image")).resolves.toMatch(/^[\da-f-]{36}$/i);
    vi.spyOn(Storage.prototype, "removeItem").mockImplementation(() => { throw new Error("storage disabled"); });
    await expect(api.clearIdempotencyKey("owner", "purchase", "image")).resolves.toBeUndefined();
  });

  test("partition fallback still separates accounts when WebCrypto is unavailable", async () => {
    vi.spyOn(crypto.subtle, "digest").mockRejectedValue(new Error("crypto disabled"));
    const first = await api.stableIdempotencyKey("owner-a", "purchase", "same-photo");
    expect(await api.stableIdempotencyKey("owner-a", "purchase", "same-photo")).toBe(first);
    expect(await api.stableIdempotencyKey("owner-b", "purchase", "same-photo")).not.toBe(first);
    expect(Object.keys(localStorage).every((key) => !key.includes("owner-"))).toBe(true);
  });

  test("text and browser FileReader fingerprints use the actual bytes", async () => {
    const known = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad";
    expect(await api.fingerprintText("abc")).toBe(known);
    expect(await api.fingerprintFile(new File(["abc"], "photo.png", { type: "image/png" }))).toBe(known);
    expect(await api.fingerprintText("abcd")).not.toBe(known);
  });
});

describe("private access and durable mutation contracts", () => {
  test.each([
    [{ detail: { code: "rate_limited", message: "请稍后再试" } }, "请稍后再试", "rate_limited"],
    [{ detail: { code: "revision_conflict" } }, "revision_conflict", "revision_conflict"],
    [{ detail: "会话已过期" }, "会话已过期", null],
    [{ detail: { reason: "unknown" } }, '{"reason":"unknown"}', null],
    [{}, "请求失败，请稍后重试", null],
  ])("preserves server failure semantics without treating them as task success: %j", async (body, message, code) => {
    vi.mocked(fetch).mockResolvedValue(new Response(JSON.stringify(body), { status: 409, headers: { "Retry-After": "12" } }));
    await expect(api.fetchAnalysisTask("owner", "task/one")).rejects.toMatchObject({ name: "ApiError", status: 409, message, code, retryAfter: "12" });
  });

  test("a non-JSON error cannot turn a denied private asset into a blob", async () => {
    vi.mocked(fetch).mockResolvedValue(new Response("not json", { status: 403 }));
    await expect(api.fetchWeddingAssetBlob("participant", "asset/one")).rejects.toThrow("请求失败");
    const [url, options] = vi.mocked(fetch).mock.calls[0];
    expect(url).toBe("/wedding-assets/asset%2Fone");
    expect(new Headers(options?.headers).get("Authorization")).toBe("Bearer participant");
    expect(String(url)).not.toContain("participant");
  });

  test("successful private media stays binary and a structured denial stays an error", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(new Response("mp4 bytes", { headers: { "Content-Type": "video/mp4" } })).mockResolvedValueOnce(reply({ detail: { message: "授权已撤回" } }, 401));
    expect(await (await api.fetchWeddingAssetBlob("owner", "preview")).text()).toBe("mp4 bytes");
    await expect(api.fetchWeddingAssetBlob("owner", "preview")).rejects.toThrow("授权已撤回");
  });

  test("cancel, explicit retry and reconciliation preserve identity and idempotency", async () => {
    vi.mocked(fetch).mockResolvedValue(reply(task("cancelled")));
    await api.cancelAnalysisTask("owner", "task/one");
    await api.retryAnalysisTask("owner", "task/one", "explicit-retry-key");
    await api.reconcileAnalysisTask("owner", "task/one");
    expect(vi.mocked(fetch).mock.calls.map(([url]) => url)).toEqual(["/v2/tasks/task%2Fone/cancel", "/v2/tasks/task%2Fone/retry", "/v2/tasks/task%2Fone/reconcile"]);
    expect(new Headers(vi.mocked(fetch).mock.calls[1][1]?.headers).get("Idempotency-Key")).toBe("explicit-retry-key");
    expect(JSON.parse(String(vi.mocked(fetch).mock.calls[2][1]?.body))).toEqual({});
  });

  test("all cursor lists preserve filters and cancellation without serializing a signal or token", async () => {
    vi.mocked(fetch).mockImplementation(async () => reply({ items: [], next_cursor: "page two", has_more: true }));
    const signal = new AbortController().signal;
    const pages = await Promise.all([
      api.fetchAnalysisTasks("owner", { status: "running", cursor: "a/b", limit: 7, signal }),
      api.fetchV2Candidates("owner", { decision_status: "bought", cursor: "a/b", limit: 7, signal }),
      api.fetchV2Feedback("owner", { cursor: "a/b", limit: 7, signal }),
      api.fetchV2WearEvents("owner", { start: "2026-01-01", end: "2026-02-01", cursor: "a/b", limit: 7, signal }),
      api.fetchV2Garments("owner", { color: "", q: "白色 衬衫", cursor: "a/b", limit: 7, signal }),
      api.fetchV2Outfits("owner", { favorite: false, fixed: true, occasion: "work", cursor: "a/b", limit: 7, signal }),
    ]);
    expect(pages.every((page) => page.next_cursor === "page two")).toBe(true);
    for (const [url, options] of vi.mocked(fetch).mock.calls) {
      const params = new URL(String(url), "http://localhost").searchParams;
      expect(params.get("cursor")).toBe("a/b"); expect(params.get("limit")).toBe("7");
      expect(params.has("signal")).toBe(false); expect(params.has("color")).toBe(false);
      expect(options?.signal).toBe(signal); expect(String(url)).not.toContain("owner");
    }
    expect(String(vi.mocked(fetch).mock.calls[5][0])).toContain("favorite=false");
  });

  test("multipart photo and purchase tasks leave the boundary to the browser", async () => {
    vi.mocked(fetch).mockResolvedValue(reply({ id: "accepted" }, 202));
    const file = new File(["photo"], "photo.png", { type: "image/png" });
    await api.uploadWeddingPhoto("participant", "project/one", file, "B");
    await api.createPurchaseImageTask("owner", file, "key", "https://shop.example/item");
    await api.createPurchaseImageTask("owner", file, "key-without-url");
    const [url, options] = vi.mocked(fetch).mock.calls[0];
    expect(url).toBe("/wedding-projects/project%2Fone/assets");
    expect((options?.body as FormData).get("person_key")).toBe("B");
    expect((options?.body as FormData).get("asset_type")).toBe("photo");
    expect((vi.mocked(fetch).mock.calls[1][1]?.body as FormData).get("product_url")).toBe("https://shop.example/item");
    expect((vi.mocked(fetch).mock.calls[2][1]?.body as FormData).has("product_url")).toBe(false);
    for (const [, init] of vi.mocked(fetch).mock.calls) expect(new Headers(init?.headers).has("Content-Type")).toBe(false);
  });

  test("public disclosure and confirmation bind a body token to immutable terms", async () => {
    const disclosure = { project_id: "p", person_key: "B" as const, display_name: "B", kind: "access" as const, terms_version: "terms-current", provider_list: ["provider"], scope: ["portrait"], purposes: ["wedding"], deletion: "30 days", expires_at: "2026-11-01", disclosure_hash: "immutable-hash" };
    vi.mocked(fetch).mockResolvedValueOnce(reply(disclosure)).mockResolvedValueOnce(reply({ accepted: true }));
    const preview = await api.previewWeddingInvite("private-invitation");
    await api.acceptWeddingInvite("private-invitation", "B", preview);
    const calls = vi.mocked(fetch).mock.calls;
    expect(calls.map(([url]) => url)).toEqual(["/consent-invites/preview", "/consent-invites/confirm"]);
    expect(JSON.parse(String(calls[1][1]?.body))).toEqual({ token: "private-invitation", accepted: true, display_name: "B", terms_version: "terms-current", disclosure_hash: "immutable-hash" });
    for (const [, options] of calls) expect(new Headers(options?.headers).has("Authorization")).toBe(false);
  });

  test("revision and media approval pin version, asset and render identities", async () => {
    vi.mocked(fetch).mockResolvedValue(reply({ id: "approved" }));
    const draft: WeddingStoryDraft = { version: 2, story_text: "事实", facts: [], shots: [] };
    await api.reviseWeddingStoryboard("owner", "project/one", draft, "current-fingerprint");
    await api.submitWeddingShotReview("participant", "shot/one", "asset", 2, "rejected");
    await api.submitWeddingRenderReview("participant", "project/one", "render/one", "preview", 2);
    await api.approveWeddingRender("owner", "project/one", "render/one");
    expect(new Headers(vi.mocked(fetch).mock.calls[0][1]?.headers).get("If-Match")).toBe("current-fingerprint");
    expect(JSON.parse(String(vi.mocked(fetch).mock.calls[1][1]?.body))).toEqual({ asset_id: "asset", project_version: 2, result: "rejected" });
    expect(JSON.parse(String(vi.mocked(fetch).mock.calls[2][1]?.body))).toEqual({ asset_id: "preview", project_version: 2, result: "approved" });
    expect(String(vi.mocked(fetch).mock.calls[3][0])).toBe("/wedding-projects/project%2Fone/approve?render_version_id=render%2Fone");
  });
});
