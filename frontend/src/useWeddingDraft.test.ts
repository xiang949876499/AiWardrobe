import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
const save = vi.hoisted(() => vi.fn());
vi.mock("./api", () => ({ saveWeddingStoryboard: save }));
import { storyToDraft, useWeddingDraft } from "./useWeddingDraft";
import type { WeddingStoryboard } from "./types";
const story: WeddingStoryboard = {
  id: "story",
  version: 2,
  style: "warm_documentary",
  status: "draft",
  draft_fingerprint: "original",
  story_text: "server",
  owner_approved_at: null,
  facts: [
    { id: "fact", source_field: "来源", text: "真实", confirmed_at: null },
  ],
  shots: [
    {
      id: "shot",
      project_version: 2,
      shot_index: 1,
      shot_type: "photo",
      duration_ms: 5000,
      person_keys: ["A"],
      fact_ids: [],
      description: "照片",
      subtitle: "",
      creative_scene: false,
      frame_asset_id: null,
      selected_asset_id: null,
      selected_candidate_id: null,
      status: "draft",
      identity_reviews: [],
    },
  ],
};
beforeEach(() => {
  localStorage.clear();
  save.mockReset();
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});
test("flush drains edits made while the first network save is in flight", async () => {
  let finish!: (story: WeddingStoryboard) => void;
  save
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    )
    .mockResolvedValue({ ...story, draft_fingerprint: "last" });
  const { result } = renderHook(() =>
    useWeddingDraft("owner", "project", true, storyToDraft(story)),
  );
  act(() => result.current.receive(story));
  act(() =>
    result.current.setDraft({
      ...result.current.draft,
      story_text: "first edit",
    }),
  );
  let flush!: Promise<WeddingStoryboard | null>;
  act(() => {
    flush = result.current.flush();
  });
  act(() =>
    result.current.setDraft({
      ...result.current.draft,
      story_text: "edit during save",
    }),
  );
  await act(async () => {
    finish({ ...story, draft_fingerprint: "next" });
    await flush;
  });
  expect(save).toHaveBeenCalledTimes(2);
  expect(save).toHaveBeenLastCalledWith(
    "owner",
    "project",
    expect.objectContaining({ story_text: "edit during save" }),
    true,
    "next",
  );
  expect(result.current.dirty).toBe(false);
});
test("incomplete facts survive re-entry at their own actor project and version without network saves", async () => {
  vi.useFakeTimers();
  const initial = {
    ...storyToDraft(story),
    facts: [{ source_field: "来源", text: "" }],
  };
  const first = renderHook(() =>
    useWeddingDraft("owner", "project", true, initial),
  );
  act(() => first.result.current.receive(null));
  act(() =>
    first.result.current.setDraft({
      ...initial,
      story_text: "原文，不虚构事实",
    }),
  );
  await act(() => vi.advanceTimersByTimeAsync(5000));
  expect(save).not.toHaveBeenCalled();
  first.unmount();
  const second = renderHook(() =>
    useWeddingDraft("owner", "project", true, initial),
  );
  act(() => second.result.current.receive(null));
  expect(second.result.current.draft.story_text).toBe("原文，不虚构事实");
  const other = renderHook(() =>
    useWeddingDraft("other", "project", true, initial),
  );
  act(() => other.result.current.receive(null));
  expect(other.result.current.draft.story_text).toBe("server");
  const newVersion = renderHook(() =>
    useWeddingDraft("owner", "project", true, { ...initial, version: 3 }),
  );
  act(() => newVersion.result.current.receive(null));
  expect(newVersion.result.current.draft.story_text).toBe("server");
});
test("the first project response initializes a missing draft at its real content version", () => {
  const hook = renderHook(
    ({ version }) =>
      useWeddingDraft("owner", "project", true, {
        ...storyToDraft(story),
        version,
      }),
    { initialProps: { version: 1 } },
  );
  hook.rerender({ version: 3 });
  act(() => hook.result.current.receive(null));
  expect(hook.result.current.draft.version).toBe(3);
});
