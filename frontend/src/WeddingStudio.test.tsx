import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const api = vi.hoisted(() => ({
  acceptWeddingInvite: vi.fn(),
  approveWeddingFrames: vi.fn(),
  approveWeddingRender: vi.fn(),
  approveWeddingStoryboard: vi.fn(),
  createWeddingGenerationJob: vi.fn(),
  createWeddingInvite: vi.fn(),
  createWeddingProject: vi.fn(),
  createWeddingQuote: vi.fn(),
  createWeddingRender: vi.fn(),
  exportWeddingRender: vi.fn(),
  fetchWeddingAssetBlob: vi.fn(),
  fetchWeddingCandidates: vi.fn(),
  fetchWeddingJobs: vi.fn(),
  fetchWeddingProject: vi.fn(),
  fetchWeddingRenders: vi.fn(),
  fetchWeddingStoryboard: vi.fn(),
  getWeddingCapabilities: vi.fn(),
  listWeddingAssets: vi.fn(),
  listWeddingProjects: vi.fn(),
  saveWeddingStoryboard: vi.fn(),
  selectWeddingCandidate: vi.fn(),
  submitWeddingRenderReview: vi.fn(),
  submitWeddingShotReview: vi.fn(),
  uploadWeddingPhoto: vi.fn(),
  confirmWeddingOwnerConsent: vi.fn(),
  acceptWeddingPhotoFallback: vi.fn(),
  regenerateWeddingShot: vi.fn(),
  revokeWeddingConsent: vi.fn(),
  deleteWeddingProject: vi.fn(),
  fetchWeddingCreationJobs: vi.fn(),
  createWeddingStoryJob: vi.fn(),
  createWeddingFrameJob: vi.fn(),
  cancelWeddingCreationJob: vi.fn(),
  fetchWeddingPhotoReadiness: vi.fn(),
  analyzeWeddingPhoto: vi.fn(),
  confirmWeddingPhotoQuality: vi.fn(),
  selectWeddingPrimaryPhoto: vi.fn(),
  fetchWeddingMusic: vi.fn(),
  selectWeddingMusic: vi.fn(),
  restoreWeddingRender: vi.fn(),
}));
vi.mock("./api", () => api);

import WeddingStudio, { PrivateMedia } from "./WeddingStudio";
import type { WeddingStoryboard } from "./types";

const capability = {
  mode: "demo" as const,
  demo_only: true,
  providers: {
    story: {
      provider: "local_demo",
      configured: true,
      available: true,
      reason: "DEMO_ONLY_NO_AI_MOTION",
    },
    frame: {
      provider: "local_demo",
      configured: true,
      available: true,
      reason: "DEMO_ONLY_NO_AI_MOTION",
    },
    character_video: {
      provider: "local_demo",
      configured: true,
      available: true,
      reason: "DEMO_ONLY_NO_AI_MOTION",
    },
    scene_video: {
      provider: "local_demo",
      configured: true,
      available: true,
      reason: "DEMO_ONLY_NO_AI_MOTION",
    },
  },
  ffmpeg_available: false,
  licensed_music_available: false,
  real_export_available: false,
};
const asset = {
  id: "candidate-asset",
  asset_type: "video_candidate",
  person_key: "A" as const,
  mime: "video/mp4",
  size_bytes: 10,
  width: 640,
  height: 360,
  generated: true,
  created_at: "2026-10-01T00:00:00Z",
};
const candidate = {
  id: "candidate-1",
  shot_id: "shot-1",
  generation_job_id: "job-1",
  asset_id: asset.id,
  asset,
  provider: "local_demo",
  model_version: "demo-1",
  quality_flags: [],
  review_result: "pending" as const,
  actual_cost_fen: 0,
  created_at: "2026-10-01T00:00:00Z",
};
const project = {
  id: "project-1",
  title: "测试婚礼",
  status: "review",
  version: 1,
  style: "warm_documentary" as const,
  target_duration_ms: 5000,
  aspect_ratio: "16:9",
  budget_limit_fen: 5000,
  budget_reserved_fen: 0,
  budget_spent_fen: 0,
  created_at: "2026-10-01T00:00:00Z",
  updated_at: "2026-10-01T00:00:00Z",
  participants: [
    {
      person_key: "A" as const,
      display_name: "小周",
      consent_status: "active" as const,
      confirmed_at: "2026-10-01T00:00:00Z",
    },
    {
      person_key: "B" as const,
      display_name: "小林",
      consent_status: "active" as const,
      confirmed_at: "2026-10-01T00:00:00Z",
    },
  ],
  story_text: "",
};

function storyboard(
  personKeys: Array<"A" | "B">,
  reviews: Array<{
    person_key: "A" | "B";
    asset_id: string;
    result: "approved" | "rejected";
    confirmed_at: string;
  }> = [],
): WeddingStoryboard {
  return {
    id: "story-1",
    version: 1,
    story_text: "一起走过",
    style: "warm_documentary" as const,
    status: "approved",
    owner_approved_at: "2026-10-01T00:00:00Z",
    facts: [
      {
        id: "fact-1",
        source_field: "相遇",
        text: "在书店",
        confirmed_at: "2026-10-01T00:00:00Z",
      },
    ],
    shots: [
      {
        id: "shot-1",
        project_version: 1,
        shot_index: 1,
        shot_type: "person_ai",
        duration_ms: 5000,
        person_keys: personKeys,
        fact_ids: ["fact-1"],
        description: "第一次相遇",
        subtitle: "那天阳光正好",
        creative_scene: false,
        frame_asset_id: null,
        selected_asset_id: null,
        selected_candidate_id: null,
        status: "candidate_review",
        identity_reviews: reviews,
      },
    ],
  };
}

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  vi.clearAllMocks();
  api.getWeddingCapabilities.mockResolvedValue(capability);
  api.fetchWeddingProject.mockResolvedValue(project);
  api.listWeddingAssets.mockResolvedValue([asset]);
  api.fetchWeddingJobs.mockResolvedValue([]);
  api.fetchWeddingCandidates.mockResolvedValue([candidate]);
  api.fetchWeddingRenders.mockResolvedValue([]);
  api.fetchWeddingAssetBlob.mockResolvedValue(new Blob(["private"]));
  api.submitWeddingShotReview.mockResolvedValue({});
  api.selectWeddingCandidate.mockResolvedValue(
    storyboard(
      ["A", "B"],
      ["A", "B"].map((person_key) => ({
        person_key: person_key as "A" | "B",
        asset_id: asset.id,
        result: "approved" as const,
        confirmed_at: "2026-10-01T00:00:00Z",
      })),
    ),
  );
  api.createWeddingQuote.mockResolvedValue({ id: "quote-1" });
  api.regenerateWeddingShot.mockResolvedValue({
    id: "job-2",
    status: "queued",
  });
  api.revokeWeddingConsent.mockResolvedValue({
    revoked: true,
    person_key: "B",
    revoked_at: "2026-10-01T00:00:00Z",
  });
  api.deleteWeddingProject.mockResolvedValue(undefined);
  api.listWeddingProjects.mockResolvedValue([]);
  api.fetchWeddingStoryboard.mockResolvedValue(storyboard(["B"]));
  api.fetchWeddingCreationJobs.mockResolvedValue([]);
  api.fetchWeddingPhotoReadiness.mockResolvedValue({
    ready: true,
    demo_bypass: true,
    people: [],
  });
  api.fetchWeddingMusic.mockResolvedValue({ available: false, tracks: [] });
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("Wedding draft safety", () => {
  test.each([false, true])(
    "a poll that began before autosave cannot replace the saved text (missing=%s)",
    async (missing) => {
      const board = {
        ...storyboard(["A"]),
        status: "draft",
        owner_approved_at: null,
        draft_fingerprint: "old",
      };
      api.fetchWeddingStoryboard.mockResolvedValue(board);
      api.saveWeddingStoryboard.mockResolvedValue({
        ...board,
        story_text: "新的已保存文字",
        draft_fingerprint: "new",
      });
      render(
        <WeddingStudio
          ownerToken="owner-token"
          initialProjectId={project.id}
        />,
      );
      await waitFor(() =>
        expect(screen.getByLabelText("你们的爱情故事")).toHaveValue("一起走过"),
      );
      let finish!: (value: typeof board) => void;
      api.fetchWeddingStoryboard.mockImplementationOnce(
        () =>
          new Promise((done, reject) => {
            finish = (value) =>
              missing ? reject(new Error("STORYBOARD_NOT_FOUND")) : done(value);
          }),
      );
      fireEvent.click(screen.getByRole("button", { name: "刷新项目" }));
      fireEvent.change(screen.getByLabelText("你们的爱情故事"), {
        target: { value: "新的已保存文字" },
      });
      await waitFor(
        () => expect(api.saveWeddingStoryboard).toHaveBeenCalled(),
        { timeout: 2000 },
      );
      await waitFor(() =>
        expect(screen.getByText("已保存")).toBeInTheDocument(),
      );
      await act(async () => finish({ ...board }));
      expect(screen.getByLabelText("你们的爱情故事")).toHaveValue(
        "新的已保存文字",
      );
    },
  );
  test("first autosave creates a draft and enables approval without a manual refresh", async () => {
    api.fetchWeddingStoryboard.mockRejectedValue(
      new Error("STORYBOARD_NOT_FOUND"),
    );
    const saved = {
      ...storyboard(["A"]),
      status: "draft",
      owner_approved_at: null,
      draft_fingerprint: "created",
    };
    api.saveWeddingStoryboard.mockResolvedValue(saved);
    render(
      <WeddingStudio ownerToken="owner-token" initialProjectId={project.id} />,
    );
    await screen.findByLabelText("你们的爱情故事");
    fireEvent.change(screen.getByLabelText("事实"), {
      target: { value: "真实事实" },
    });
    await waitFor(() => expect(api.saveWeddingStoryboard).toHaveBeenCalled());
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "新人确认分镜" }),
      ).toBeEnabled(),
    );
  });
  function editable() {
    return {
      ...storyboard(["A"]),
      status: "draft",
      owner_approved_at: null,
      draft_fingerprint: "fingerprint-1",
    };
  }
  test("autosaves after one second with a fingerprint and flushes before approval", async () => {
    api.fetchWeddingStoryboard.mockResolvedValue(editable());
    api.saveWeddingStoryboard.mockResolvedValue({
      ...editable(),
      draft_fingerprint: "fingerprint-2",
    });
    api.approveWeddingStoryboard.mockResolvedValue(storyboard(["A"]));
    render(
      <WeddingStudio ownerToken="owner-token" initialProjectId={project.id} />,
    );
    const editor = await screen.findByLabelText("你们的爱情故事");
    vi.useFakeTimers();
    fireEvent.change(editor, { target: { value: "本地编辑" } });
    await act(() => vi.advanceTimersByTimeAsync(999));
    expect(api.saveWeddingStoryboard).not.toHaveBeenCalled();
    await act(() => vi.advanceTimersByTimeAsync(1));
    expect(api.saveWeddingStoryboard).toHaveBeenCalledWith(
      "owner-token",
      project.id,
      expect.objectContaining({ story_text: "本地编辑" }),
      true,
      "fingerprint-1",
    );
    fireEvent.change(editor, { target: { value: "批准前的最后一句" } });
    await act(async () =>
      fireEvent.click(screen.getByRole("button", { name: "新人确认分镜" })),
    );
    expect(api.saveWeddingStoryboard).toHaveBeenLastCalledWith(
      "owner-token",
      project.id,
      expect.objectContaining({ story_text: "批准前的最后一句" }),
      true,
      "fingerprint-2",
    );
    expect(api.approveWeddingStoryboard).toHaveBeenCalledWith(
      "owner-token",
      project.id,
      1,
      "fingerprint-2",
    );
  });
  test("restores local recovery after reload and retains it on conflict", async () => {
    api.fetchWeddingStoryboard.mockResolvedValue(editable());
    api.saveWeddingStoryboard.mockRejectedValue(new Error("DRAFT_CONFLICT"));
    const view = render(
      <WeddingStudio ownerToken="owner-token" initialProjectId={project.id} />,
    );
    await waitFor(() =>
      expect(screen.getByLabelText("你们的爱情故事")).toHaveValue("一起走过"),
    );
    fireEvent.change(await screen.findByLabelText("你们的爱情故事"), {
      target: { value: "仍未保存的故事" },
    });
    view.unmount();
    render(
      <WeddingStudio ownerToken="owner-token" initialProjectId={project.id} />,
    );
    await waitFor(() =>
      expect(screen.getByLabelText("你们的爱情故事")).toHaveValue(
        "仍未保存的故事",
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: "保存分镜草稿" }));
    await screen.findByText(/草稿冲突/);
    expect(screen.getByLabelText("你们的爱情故事")).toHaveValue(
      "仍未保存的故事",
    );
    fireEvent.click(screen.getByRole("button", { name: "新人确认分镜" }));
    expect(api.approveWeddingStoryboard).not.toHaveBeenCalled();
  });
  test("polling keeps dirty text and participant never requests owner creation jobs", async () => {
    api.fetchWeddingStoryboard.mockResolvedValue(editable());
    api.fetchWeddingJobs.mockResolvedValue([
      { id: "poll", status: "processing", completed_shots: 0, total_shots: 12 },
    ]);
    api.saveWeddingStoryboard.mockRejectedValue(new Error("offline"));
    const view = render(
      <WeddingStudio ownerToken="owner-token" initialProjectId={project.id} />,
    );
    const editor = await screen.findByLabelText("你们的爱情故事");
    vi.useFakeTimers();
    fireEvent.change(editor, { target: { value: "不要被轮询替换" } });
    await act(() => vi.advanceTimersByTimeAsync(3000));
    expect(editor).toHaveValue("不要被轮询替换");
    view.unmount();
    vi.useRealTimers();
    api.fetchWeddingCreationJobs.mockClear();
    sessionStorage.setItem(
      `wedding_participant:${project.id}`,
      "participant-token",
    );
    render(<WeddingStudio ownerToken={null} initialProjectId={project.id} />);
    await screen.findByLabelText("你们的爱情故事");
    expect(api.fetchWeddingCreationJobs).not.toHaveBeenCalled();
    expect(
      screen.queryByRole("button", { name: "AI 编排故事" }),
    ).not.toBeInTheDocument();
  });
  test("account and project changes hide private old media immediately and fence late fetches", async () => {
    let resolve!: (blob: Blob) => void;
    api.fetchWeddingAssetBlob.mockImplementationOnce(
      () =>
        new Promise<Blob>((done) => {
          resolve = done;
        }),
    );
    vi.stubGlobal(
      "URL",
      Object.assign(URL, {
        createObjectURL: vi.fn(() => "blob:private-old"),
        revokeObjectURL: vi.fn(),
      }),
    );
    const view = render(
      <WeddingStudio ownerToken="owner-token" initialProjectId={project.id} />,
    );
    await screen.findByText("local_demo · demo-1");
    api.fetchWeddingProject.mockImplementation(() => new Promise(() => {}));
    view.rerender(
      <WeddingStudio ownerToken="other-owner" initialProjectId="project-2" />,
    );
    expect(screen.queryByText("local_demo · demo-1")).not.toBeInTheDocument();
    await act(async () => resolve(new Blob(["old"])));
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:private-old");
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  });
  test("story queue uses raw text even with an incomplete local fact", async () => {
    api.fetchWeddingStoryboard.mockRejectedValue(
      new Error("STORYBOARD_NOT_FOUND"),
    );
    api.createWeddingStoryJob.mockResolvedValue({ id: "story-job" });
    render(
      <WeddingStudio ownerToken="owner-token" initialProjectId={project.id} />,
    );
    fireEvent.change(await screen.findByLabelText("你们的爱情故事"), {
      target: { value: "真实原文" },
    });
    fireEvent.click(screen.getByRole("button", { name: "AI 编排故事" }));
    await waitFor(() =>
      expect(api.createWeddingStoryJob).toHaveBeenCalledWith(
        "owner-token",
        project.id,
        1,
        "真实原文",
      ),
    );
    expect(api.saveWeddingStoryboard).not.toHaveBeenCalled();
  });
  test("late project refresh cannot populate another actor's current project", async () => {
    let finish!: (value: typeof project) => void;
    api.fetchWeddingProject.mockImplementationOnce(
      () =>
        new Promise((done) => {
          finish = done;
        }),
    );
    const view = render(
      <WeddingStudio ownerToken="owner-token" initialProjectId={project.id} />,
    );
    api.fetchWeddingProject.mockResolvedValue({
      ...project,
      id: "second",
      title: "另一个私密项目",
    });
    view.rerender(
      <WeddingStudio ownerToken="new-owner" initialProjectId="second" />,
    );
    await screen.findByText("另一个私密项目");
    await act(async () => finish(project));
    expect(screen.queryByText("测试婚礼")).not.toBeInTheDocument();
  });
  test("private media hides loaded scope before a new asset fetch and ignores old rejection", async () => {
    vi.stubGlobal(
      "URL",
      Object.assign(URL, {
        createObjectURL: vi.fn(() => "blob:first"),
        revokeObjectURL: vi.fn(),
      }),
    );
    const view = render(<PrivateMedia token="owner-1" assetId="first" />);
    expect(await screen.findByRole("img")).toHaveAttribute("src", "blob:first");
    api.fetchWeddingAssetBlob.mockImplementationOnce(
      () => new Promise(() => {}),
    );
    view.rerender(<PrivateMedia token="owner-2" assetId="second" />);
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:first");
  });
});

describe("Wedding workflow actions", () => {
  const historical = {
    id: "render-old",
    project_version: 1,
    version: 1,
    status: "approved",
    preview_asset_id: "preview-old",
    export_asset_id: "export-old",
    output_duration_ms: 5000,
    owner_approved_at: "2026-10-01",
    created_at: "2026-10-01",
    identity_reviews: [
      { person_key: "A", asset_id: "preview-old", result: "approved" },
      { person_key: "B", asset_id: "preview-old", result: "approved" },
    ],
    timeline: [
      {
        shot_id: "shot-1",
        shot_index: 1,
        subtitle: "旧字幕",
        duration_ms: 5000,
        asset_id: "candidate-asset",
      },
    ],
    invalidated_at: null,
  };
  test("selected history drives review, approval, export, subtitle edit and restore", async () => {
    api.fetchWeddingRenders.mockResolvedValue([
      {
        ...historical,
        id: "render-new",
        version: 2,
        preview_asset_id: "preview-new",
        status: "queued",
        identity_reviews: [],
      },
      historical,
    ]);
    api.exportWeddingRender.mockResolvedValue({ asset_id: "export-old" });
    api.restoreWeddingRender.mockResolvedValue({
      ...historical,
      id: "restored",
    });
    api.createWeddingRender.mockResolvedValue({ ...historical, id: "edited" });
    vi.stubGlobal(
      "URL",
      Object.assign(URL, {
        createObjectURL: vi.fn(() => "blob:test"),
        revokeObjectURL: vi.fn(),
      }),
    );
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    render(
      <WeddingStudio ownerToken="owner-token" initialProjectId={project.id} />,
    );
    fireEvent.change(await screen.findByLabelText("成片历史版本"), {
      target: { value: "render-old" },
    });
    fireEvent.click(screen.getByRole("button", { name: "导出成片" }));
    await waitFor(() =>
      expect(api.exportWeddingRender).toHaveBeenCalledWith(
        "owner-token",
        project.id,
        "render-old",
      ),
    );
    fireEvent.change(screen.getByLabelText("镜头 1 字幕"), {
      target: { value: "新字幕" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "保存字幕/音乐为新版本" }),
    );
    await waitFor(() =>
      expect(api.createWeddingRender).toHaveBeenCalledWith(
        "owner-token",
        project.id,
        1,
        { source_render_id: "render-old", subtitles: { "shot-1": "新字幕" } },
      ),
    );
    fireEvent.change(screen.getByLabelText("成片历史版本"), {
      target: { value: "render-old" },
    });
    fireEvent.click(screen.getByRole("button", { name: "恢复所选版本" }));
    await waitFor(() =>
      expect(api.restoreWeddingRender).toHaveBeenCalledWith(
        "owner-token",
        project.id,
        "render-old",
        1,
      ),
    );
  });
  test("photo readiness, own primary, licensed catalog and frame assignments use explicit actions", async () => {
    api.fetchWeddingPhotoReadiness.mockResolvedValue({
      ready: false,
      demo_bypass: false,
      people: [
        {
          person_key: "A",
          usable_count: 2,
          required_count: 3,
          primary_photo_asset_id: null,
          ready: false,
          warnings_unconfirmed: ["photo-a"],
          photos: [
            {
              asset_id: "photo-a",
              quality: { usable: true, warnings: ["blur"] },
              confirmed_at: null,
            },
          ],
        },
      ],
    });
    api.listWeddingAssets.mockResolvedValue([
      { ...asset, id: "photo-a", asset_type: "photo", person_key: "A" },
    ]);
    api.fetchWeddingMusic.mockResolvedValue({
      available: true,
      tracks: [
        {
          track_id: "track-1",
          title: "授权曲目",
          license_id: "license-1",
          license_version: "v1",
          allowed_uses: ["customer_embedded_delivery", "wedding_playback"],
          perpetual_embedded_delivery: true,
        },
      ],
    });
    api.selectWeddingPrimaryPhoto.mockResolvedValue({
      cost_notice: "需要重新生成，可能收费",
      affected_shot_ids: ["shot-1"],
      regeneration_required: true,
    });
    api.confirmWeddingPhotoQuality.mockResolvedValue({});
    api.selectWeddingMusic.mockResolvedValue({});
    render(
      <WeddingStudio ownerToken="owner-token" initialProjectId={project.id} />,
    );
    await screen.findByText(/A 方：2\/3/);
    fireEvent.click(
      screen.getByRole("button", { name: "确认 photo-a 的质量提醒" }),
    );
    await waitFor(() =>
      expect(api.confirmWeddingPhotoQuality).toHaveBeenCalledWith(
        "owner-token",
        project.id,
        "photo-a",
        true,
      ),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "选择 photo-a 为本人主参考" }),
    );
    await screen.findByText("需要重新生成，可能收费");
    expect(api.selectWeddingPrimaryPhoto).toHaveBeenCalledWith(
      "owner-token",
      project.id,
      "A",
      1,
      "photo-a",
    );
    fireEvent.change(screen.getByLabelText("授权音乐"), {
      target: { value: "track-1" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存音乐选择" }));
    await waitFor(() =>
      expect(api.selectWeddingMusic).toHaveBeenCalledWith(
        "owner-token",
        project.id,
        "track-1",
      ),
    );
  });
  test("unknown creation outcome never offers paid retry", async () => {
    api.fetchWeddingCreationJobs.mockResolvedValue([
      {
        id: "unknown",
        kind: "story",
        status: "needs_action",
        error_code: "PROVIDER_OUTCOME_UNKNOWN",
        completed_operations: 0,
        total_operations: 1,
        demo: false,
      },
    ]);
    render(
      <WeddingStudio ownerToken="owner-token" initialProjectId={project.id} />,
    );
    await screen.findByText(/结果未知：可能已收费/);
    expect(
      screen.queryByRole("button", { name: /重试.*story/ }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "AI 编排故事" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "提交生成任务" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "重做此镜头" })).toBeDisabled();
  });
  test("owner approval and participant review address the selected render", async () => {
    api.fetchWeddingRenders.mockResolvedValue([
      { ...historical, id: "new", version: 2, status: "queued" },
      { ...historical, status: "ready" },
    ]);
    api.approveWeddingRender.mockResolvedValue({
      ...historical,
      status: "approved",
    });
    const view = render(
      <WeddingStudio ownerToken="owner-token" initialProjectId={project.id} />,
    );
    fireEvent.change(await screen.findByLabelText("成片历史版本"), {
      target: { value: "render-old" },
    });
    fireEvent.click(screen.getByRole("button", { name: "确认双方验收" }));
    await waitFor(() =>
      expect(api.approveWeddingRender).toHaveBeenCalledWith(
        "owner-token",
        project.id,
        "render-old",
      ),
    );
    view.unmount();
    sessionStorage.setItem(
      `wedding_participant:${project.id}`,
      "participant-token",
    );
    api.fetchWeddingRenders.mockResolvedValue([
      { ...historical, id: "new", version: 2, status: "queued" },
      { ...historical, status: "ready", identity_reviews: [] },
    ]);
    api.submitWeddingRenderReview.mockResolvedValue({});
    render(<WeddingStudio ownerToken={null} initialProjectId={project.id} />);
    fireEvent.change(await screen.findByLabelText("成片历史版本"), {
      target: { value: "render-old" },
    });
    fireEvent.click(screen.getByRole("button", { name: "我确认这版预览" }));
    await waitFor(() =>
      expect(api.submitWeddingRenderReview).toHaveBeenCalledWith(
        "participant-token",
        project.id,
        "render-old",
        "preview-old",
        1,
      ),
    );
  });
  test("frame queue sends all six explicit original photo assignments", async () => {
    const board = storyboard(["A"]);
    board.shots = Array.from({ length: 6 }, (_, index) => ({
      ...board.shots[0],
      id: `photo-shot-${index}`,
      shot_index: index + 1,
      shot_type: "photo",
      frame_asset_id: `original-${index}`,
    })) as typeof board.shots;
    api.fetchWeddingStoryboard.mockResolvedValue(board);
    api.listWeddingAssets.mockResolvedValue(
      Array.from({ length: 6 }, (_, index) => ({
        ...asset,
        id: `original-${index}`,
        asset_type: "photo",
      })),
    );
    api.createWeddingFrameJob.mockResolvedValue({ id: "frame-job" });
    render(
      <WeddingStudio ownerToken="owner-token" initialProjectId={project.id} />,
    );
    fireEvent.click(
      await screen.findByRole("button", { name: "AI 制作关键帧" }),
    );
    await waitFor(() =>
      expect(api.createWeddingFrameJob).toHaveBeenCalledWith(
        "owner-token",
        project.id,
        1,
        Array.from({ length: 6 }, (_, index) => ({
          shot_id: `photo-shot-${index}`,
          asset_id: `original-${index}`,
        })),
      ),
    );
  });
  test("queued creation can be cancelled and a known terminal failure allows an explicit retry", async () => {
    api.fetchWeddingStoryboard.mockResolvedValue({
      ...storyboard(["A"]),
      status: "draft",
      owner_approved_at: null,
      draft_fingerprint: "original",
    });
    api.fetchWeddingCreationJobs.mockResolvedValue([
      {
        id: "queued-story",
        kind: "story",
        status: "queued",
        completed_operations: 0,
        total_operations: 1,
        demo: true,
        error_code: null,
      },
    ]);
    api.cancelWeddingCreationJob.mockResolvedValue({
      id: "queued-story",
      status: "cancelled",
    });
    api.createWeddingStoryJob.mockResolvedValue({ id: "replacement" });
    render(
      <WeddingStudio ownerToken="owner-token" initialProjectId={project.id} />,
    );
    fireEvent.click(
      await screen.findByRole("button", { name: "取消story任务" }),
    );
    await waitFor(() =>
      expect(api.cancelWeddingCreationJob).toHaveBeenCalledWith(
        "owner-token",
        project.id,
        "queued-story",
      ),
    );
    api.fetchWeddingCreationJobs.mockResolvedValue([
      {
        id: "failed-story",
        kind: "story",
        status: "needs_action",
        completed_operations: 0,
        total_operations: 1,
        demo: false,
        error_code: "PROVIDER_RESULT_INVALID",
      },
    ]);
    fireEvent.click(screen.getByRole("button", { name: "刷新项目" }));
    fireEvent.click(
      await screen.findByRole("button", {
        name: "重试 story（LIVE 可能重新收费）",
      }),
    );
    await waitFor(() =>
      expect(api.createWeddingStoryJob).toHaveBeenCalledWith(
        "owner-token",
        project.id,
        1,
        "一起走过",
      ),
    );
  });
  test("quality reanalysis and withdrawal are own-person actions and stale history cannot be played", async () => {
    api.fetchWeddingPhotoReadiness.mockResolvedValue({
      ready: false,
      demo_bypass: false,
      people: [
        {
          person_key: "A",
          usable_count: 3,
          required_count: 3,
          primary_photo_asset_id: "photo-a",
          ready: true,
          warnings_unconfirmed: [],
          photos: [
            {
              asset_id: "photo-a",
              quality: { usable: true, warnings: ["blur"] },
              confirmed_at: "2026-10-01",
            },
          ],
        },
        {
          person_key: "B",
          usable_count: 0,
          required_count: 3,
          primary_photo_asset_id: null,
          ready: false,
          warnings_unconfirmed: [],
          photos: [
            {
              asset_id: "photo-b",
              quality: { usable: false, warnings: ["multiple_faces"] },
              confirmed_at: null,
            },
          ],
        },
      ],
    });
    api.analyzeWeddingPhoto.mockResolvedValue({});
    api.confirmWeddingPhotoQuality.mockResolvedValue({});
    api.fetchWeddingRenders.mockResolvedValue([
      {
        ...historical,
        invalidated_at: "2026-10-02",
        error_code: "RENDER_HISTORY_INVALID",
        attempts: 3,
      },
    ]);
    render(
      <WeddingStudio ownerToken="owner-token" initialProjectId={project.id} />,
    );
    fireEvent.click(
      await screen.findByRole("button", { name: "检测 photo-a 的质量" }),
    );
    await waitFor(() =>
      expect(api.analyzeWeddingPhoto).toHaveBeenCalledWith(
        "owner-token",
        project.id,
        "photo-a",
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: "撤回质量确认" }));
    await waitFor(() =>
      expect(api.confirmWeddingPhotoQuality).toHaveBeenCalledWith(
        "owner-token",
        project.id,
        "photo-a",
        false,
      ),
    );
    expect(
      screen.queryByRole("button", { name: "选择 photo-b 为本人主参考" }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "恢复所选版本" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "导出成片" })).toBeDisabled();
    expect(screen.queryByLabelText("婚礼 MV 私密预览")).not.toBeInTheDocument();
  });
});

describe("Wedding Studio candidate review and regeneration", () => {
  test("live MP4 candidate is playable while a demo PNG candidate remains an image", async () => {
    vi.stubGlobal(
      "URL",
      Object.assign(URL, {
        createObjectURL: vi.fn(() => "blob:motion"),
        revokeObjectURL: vi.fn(),
      }),
    );
    api.getWeddingCapabilities.mockResolvedValue({
      ...capability,
      mode: "live",
      demo_only: false,
    });
    const view = render(
      <WeddingStudio ownerToken="owner-token" initialProjectId={project.id} />,
    );
    expect(await screen.findByLabelText("镜头候选私密视频")).toHaveAttribute(
      "src",
      "blob:motion",
    );
    expect(screen.getByLabelText("镜头候选私密视频").tagName).toBe("VIDEO");
    view.unmount();
    api.fetchWeddingCandidates.mockResolvedValue([
      { ...candidate, asset: { ...asset, mime: "image/png" } },
    ]);
    render(
      <WeddingStudio ownerToken="owner-token" initialProjectId={project.id} />,
    );
    expect(await screen.findByRole("img")).toHaveAttribute(
      "src",
      "blob:motion",
    );
    expect(screen.queryByLabelText("镜头候选私密视频")).not.toBeInTheDocument();
  });
  test("participant can approve or reject their candidate and cannot regenerate a shot", async () => {
    const user = userEvent.setup();
    sessionStorage.setItem("wedding_active_project", project.id);
    sessionStorage.setItem(
      `wedding_participant:${project.id}`,
      "participant-token",
    );
    render(<WeddingStudio ownerToken={null} initialProjectId={project.id} />);

    await screen.findByText("local_demo · demo-1");
    await user.click(screen.getByRole("button", { name: "拒绝候选" }));
    await user.click(screen.getByRole("button", { name: "我确认候选" }));

    expect(api.submitWeddingShotReview).toHaveBeenNthCalledWith(
      1,
      "participant-token",
      "shot-1",
      asset.id,
      1,
      "rejected",
    );
    expect(api.submitWeddingShotReview).toHaveBeenNthCalledWith(
      2,
      "participant-token",
      "shot-1",
      asset.id,
      1,
      "approved",
    );
    expect(
      screen.queryByRole("button", { name: "重做此镜头" }),
    ).not.toBeInTheDocument();
  });

  test("owner can select a pending candidate only after both people approved the same asset and can regenerate that shot", async () => {
    const user = userEvent.setup();
    const reviews = ["A", "B"].map((person_key) => ({
      person_key: person_key as "A" | "B",
      asset_id: asset.id,
      result: "approved" as const,
      confirmed_at: "2026-10-01T00:00:00Z",
    }));
    api.fetchWeddingStoryboard.mockResolvedValue(
      storyboard(["A", "B"], reviews),
    );
    render(
      <WeddingStudio ownerToken="owner-token" initialProjectId={project.id} />,
    );

    const select = await screen.findByRole("button", { name: "选用" });
    expect(select).toBeEnabled();
    await user.click(select);
    expect(api.selectWeddingCandidate).toHaveBeenCalledWith(
      "owner-token",
      project.id,
      "shot-1",
      candidate.id,
      1,
    );
    await user.click(screen.getByRole("button", { name: "重做此镜头" }));
    await waitFor(() =>
      expect(api.regenerateWeddingShot).toHaveBeenCalledWith(
        "owner-token",
        project.id,
        "shot-1",
        "quote-1",
        1,
      ),
    );
  });

  test("participant can revoke consent and the browser session token is removed", async () => {
    const user = userEvent.setup();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    sessionStorage.setItem("wedding_active_project", project.id);
    sessionStorage.setItem(
      `wedding_participant:${project.id}`,
      "participant-token",
    );
    render(<WeddingStudio ownerToken={null} initialProjectId={project.id} />);

    await user.click(
      await screen.findByRole("button", { name: "撤回我的授权" }),
    );

    await waitFor(() =>
      expect(api.revokeWeddingConsent).toHaveBeenCalledWith(
        "participant-token",
        project.id,
      ),
    );
    expect(
      sessionStorage.getItem(`wedding_participant:${project.id}`),
    ).toBeNull();
    expect(
      screen.getByRole("heading", { name: "婚礼 MV 项目需要访问令牌" }),
    ).toBeInTheDocument();
    confirm.mockRestore();
  });

  test("owner must confirm project deletion and returns to their project list", async () => {
    const user = userEvent.setup();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    sessionStorage.setItem("wedding_active_project", project.id);
    render(
      <WeddingStudio ownerToken="owner-token" initialProjectId={project.id} />,
    );

    await user.click(await screen.findByRole("button", { name: "删除项目" }));

    await waitFor(() =>
      expect(api.deleteWeddingProject).toHaveBeenCalledWith(
        "owner-token",
        project.id,
      ),
    );
    expect(sessionStorage.getItem("wedding_active_project")).toBeNull();
    expect(
      await screen.findByRole("heading", { name: "开始共同创作" }),
    ).toBeInTheDocument();
    confirm.mockRestore();
  });
});
