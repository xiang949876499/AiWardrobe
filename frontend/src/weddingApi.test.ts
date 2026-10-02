import { afterEach, expect, test, vi } from "vitest";
import {
  saveWeddingStoryboard,
  approveWeddingStoryboard,
  createWeddingStoryJob,
  createWeddingFrameJob,
  createWeddingRender,
  exportWeddingRender,
  restoreWeddingRender,
  createWeddingQuote,
} from "./api";
import { EMPTY_DRAFT_FINGERPRINT } from "./useWeddingDraft";
import type { WeddingStoryDraft } from "./types";
afterEach(() => vi.unstubAllGlobals());
test("authoring requests carry CAS, scoped selectors and explicit creation idempotency", async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValue({ ok: true, json: async () => ({}) });
  vi.stubGlobal("fetch", fetcher);
  const draft: WeddingStoryDraft = {
    version: 1,
    story_text: "原文",
    facts: [],
    shots: [],
  };
  await saveWeddingStoryboard(
    "token",
    "project",
    draft,
    false,
    EMPTY_DRAFT_FINGERPRINT,
  );
  await approveWeddingStoryboard("token", "project", 1, "fingerprint");
  await createWeddingStoryJob(
    "token",
    "project",
    1,
    "未完整事实仍可编排的原文",
  );
  await createWeddingFrameJob("token", "project", 1, [
    { shot_id: "photo-shot", asset_id: "photo" },
  ]);
  await createWeddingRender("token", "project", 1, {
    source_render_id: "history",
    subtitles: { shot: "新字幕" },
  });
  await exportWeddingRender("token", "project", "history");
  await restoreWeddingRender("token", "project", "history", 1);
  await createWeddingQuote("token", "project", "single-shot");
  const options = (index: number) => fetcher.mock.calls[index][1];
  expect(new Headers(options(0).headers).get("If-Match")).toBe(
    EMPTY_DRAFT_FINGERPRINT,
  );
  expect(options(0).method).toBe("POST");
  expect(new Headers(options(1).headers).get("If-Match")).toBe("fingerprint");
  expect(JSON.parse(options(2).body)).toEqual({
    version: 1,
    story_text: "未完整事实仍可编排的原文",
  });
  expect(new Headers(options(2).headers).get("Idempotency-Key")).toBeTruthy();
  expect(JSON.parse(options(3).body).photos).toEqual([
    { shot_id: "photo-shot", asset_id: "photo" },
  ]);
  expect(JSON.parse(options(4).body).source_render_id).toBe("history");
  expect(fetcher.mock.calls[5][0]).toBe(
    "/wedding-projects/project/export?render_version_id=history",
  );
  expect(fetcher.mock.calls[6][0]).toBe(
    "/wedding-projects/project/renders/history/restore",
  );
  expect(fetcher.mock.calls[7][0]).toBe(
    "/wedding-projects/project/quote?shot_id=single-shot",
  );
});
