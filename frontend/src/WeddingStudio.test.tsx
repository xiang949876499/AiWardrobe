import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const api = vi.hoisted(() => ({
  acceptWeddingInvite: vi.fn(), approveWeddingFrames: vi.fn(), approveWeddingRender: vi.fn(), approveWeddingStoryboard: vi.fn(),
  createWeddingGenerationJob: vi.fn(), createWeddingInvite: vi.fn(), createWeddingProject: vi.fn(), createWeddingQuote: vi.fn(),
  createWeddingRender: vi.fn(), exportWeddingRender: vi.fn(), fetchWeddingAssetBlob: vi.fn(), fetchWeddingCandidates: vi.fn(),
  fetchWeddingJobs: vi.fn(), fetchWeddingProject: vi.fn(), fetchWeddingRenders: vi.fn(), fetchWeddingStoryboard: vi.fn(),
  getWeddingCapabilities: vi.fn(), listWeddingAssets: vi.fn(), listWeddingProjects: vi.fn(), saveWeddingStoryboard: vi.fn(),
  selectWeddingCandidate: vi.fn(), submitWeddingRenderReview: vi.fn(), submitWeddingShotReview: vi.fn(), uploadWeddingPhoto: vi.fn(),
  confirmWeddingOwnerConsent: vi.fn(), acceptWeddingPhotoFallback: vi.fn(), regenerateWeddingShot: vi.fn(),
  revokeWeddingConsent: vi.fn(), deleteWeddingProject: vi.fn()
}));
vi.mock("./api", () => api);

import WeddingStudio from "./WeddingStudio";

const capability = {
  mode: "demo" as const, demo_only: true,
  providers: { story: { provider: "local_demo", configured: true, available: true, reason: "DEMO_ONLY_NO_AI_MOTION" }, frame: { provider: "local_demo", configured: true, available: true, reason: "DEMO_ONLY_NO_AI_MOTION" }, character_video: { provider: "local_demo", configured: true, available: true, reason: "DEMO_ONLY_NO_AI_MOTION" }, scene_video: { provider: "local_demo", configured: true, available: true, reason: "DEMO_ONLY_NO_AI_MOTION" } },
  ffmpeg_available: false, licensed_music_available: false, real_export_available: false
};
const asset = { id: "candidate-asset", asset_type: "video_candidate", person_key: "A" as const, mime: "video/mp4", size_bytes: 10, width: 640, height: 360, generated: true, created_at: "2026-10-01T00:00:00Z" };
const candidate = { id: "candidate-1", shot_id: "shot-1", generation_job_id: "job-1", asset_id: asset.id, asset, provider: "local_demo", model_version: "demo-1", quality_flags: [], review_result: "pending" as const, actual_cost_fen: 0, created_at: "2026-10-01T00:00:00Z" };
const project = { id: "project-1", title: "测试婚礼", status: "review", version: 1, style: "warm_documentary" as const, target_duration_ms: 5000, aspect_ratio: "16:9", budget_limit_fen: 5000, budget_reserved_fen: 0, budget_spent_fen: 0, created_at: "2026-10-01T00:00:00Z", updated_at: "2026-10-01T00:00:00Z", participants: [{ person_key: "A" as const, display_name: "小周", consent_status: "active" as const, confirmed_at: "2026-10-01T00:00:00Z" }, { person_key: "B" as const, display_name: "小林", consent_status: "active" as const, confirmed_at: "2026-10-01T00:00:00Z" }], story_text: "" };

function storyboard(personKeys: Array<"A" | "B">, reviews: Array<{ person_key: "A" | "B"; asset_id: string; result: "approved" | "rejected"; confirmed_at: string }> = []) {
  return { id: "story-1", version: 1, story_text: "一起走过", style: "warm_documentary" as const, status: "approved", owner_approved_at: "2026-10-01T00:00:00Z", facts: [{ id: "fact-1", source_field: "相遇", text: "在书店", confirmed_at: "2026-10-01T00:00:00Z" }], shots: [{ id: "shot-1", project_version: 1, shot_index: 1, shot_type: "person_ai", duration_ms: 5000, person_keys: personKeys, fact_ids: ["fact-1"], description: "第一次相遇", subtitle: "那天阳光正好", creative_scene: false, frame_asset_id: null, selected_asset_id: null, selected_candidate_id: null, status: "candidate_review", identity_reviews: reviews }] };
}

beforeEach(() => {
  localStorage.clear(); sessionStorage.clear();
  vi.clearAllMocks();
  api.getWeddingCapabilities.mockResolvedValue(capability);
  api.fetchWeddingProject.mockResolvedValue(project);
  api.listWeddingAssets.mockResolvedValue([asset]);
  api.fetchWeddingJobs.mockResolvedValue([]);
  api.fetchWeddingCandidates.mockResolvedValue([candidate]);
  api.fetchWeddingRenders.mockResolvedValue([]);
  api.fetchWeddingAssetBlob.mockResolvedValue(new Blob(["private"]));
  api.submitWeddingShotReview.mockResolvedValue({});
  api.selectWeddingCandidate.mockResolvedValue(storyboard(["A", "B"], ["A", "B"].map((person_key) => ({ person_key: person_key as "A" | "B", asset_id: asset.id, result: "approved" as const, confirmed_at: "2026-10-01T00:00:00Z" }))));
  api.createWeddingQuote.mockResolvedValue({ id: "quote-1" });
  api.regenerateWeddingShot.mockResolvedValue({ id: "job-2", status: "queued" });
  api.revokeWeddingConsent.mockResolvedValue({ revoked: true, person_key: "B", revoked_at: "2026-10-01T00:00:00Z" });
  api.deleteWeddingProject.mockResolvedValue(undefined);
  api.listWeddingProjects.mockResolvedValue([]);
  api.fetchWeddingStoryboard.mockResolvedValue(storyboard(["B"]));
});

afterEach(() => cleanup());

describe("Wedding Studio candidate review and regeneration", () => {
  test("participant can approve or reject their candidate and cannot regenerate a shot", async () => {
    const user = userEvent.setup();
    sessionStorage.setItem("wedding_active_project", project.id);
    sessionStorage.setItem(`wedding_participant:${project.id}`, "participant-token");
    render(<WeddingStudio ownerToken={null} initialProjectId={project.id} />);

    await screen.findByText("local_demo · demo-1");
    await user.click(screen.getByRole("button", { name: "拒绝候选" }));
    await user.click(screen.getByRole("button", { name: "我确认候选" }));

    expect(api.submitWeddingShotReview).toHaveBeenNthCalledWith(1, "participant-token", "shot-1", asset.id, 1, "rejected");
    expect(api.submitWeddingShotReview).toHaveBeenNthCalledWith(2, "participant-token", "shot-1", asset.id, 1, "approved");
    expect(screen.queryByRole("button", { name: "重做此镜头" })).not.toBeInTheDocument();
  });

  test("owner can select a pending candidate only after both people approved the same asset and can regenerate that shot", async () => {
    const user = userEvent.setup();
    const reviews = ["A", "B"].map((person_key) => ({ person_key: person_key as "A" | "B", asset_id: asset.id, result: "approved" as const, confirmed_at: "2026-10-01T00:00:00Z" }));
    api.fetchWeddingStoryboard.mockResolvedValue(storyboard(["A", "B"], reviews));
    render(<WeddingStudio ownerToken="owner-token" initialProjectId={project.id} />);

    const select = await screen.findByRole("button", { name: "选用" });
    expect(select).toBeEnabled();
    await user.click(select);
    expect(api.selectWeddingCandidate).toHaveBeenCalledWith("owner-token", project.id, "shot-1", candidate.id, 1);
    await user.click(screen.getByRole("button", { name: "重做此镜头" }));
    await waitFor(() => expect(api.regenerateWeddingShot).toHaveBeenCalledWith("owner-token", project.id, "shot-1", "quote-1", 1));
  });

  test("participant can revoke consent and the browser session token is removed", async () => {
    const user = userEvent.setup();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    sessionStorage.setItem("wedding_active_project", project.id);
    sessionStorage.setItem(`wedding_participant:${project.id}`, "participant-token");
    render(<WeddingStudio ownerToken={null} initialProjectId={project.id} />);

    await user.click(await screen.findByRole("button", { name: "撤回我的授权" }));

    await waitFor(() => expect(api.revokeWeddingConsent).toHaveBeenCalledWith("participant-token", project.id));
    expect(sessionStorage.getItem(`wedding_participant:${project.id}`)).toBeNull();
    expect(screen.getByRole("heading", { name: "婚礼 MV 项目需要访问令牌" })).toBeInTheDocument();
    confirm.mockRestore();
  });

  test("owner must confirm project deletion and returns to their project list", async () => {
    const user = userEvent.setup();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    sessionStorage.setItem("wedding_active_project", project.id);
    render(<WeddingStudio ownerToken="owner-token" initialProjectId={project.id} />);

    await user.click(await screen.findByRole("button", { name: "删除项目" }));

    await waitFor(() => expect(api.deleteWeddingProject).toHaveBeenCalledWith("owner-token", project.id));
    expect(sessionStorage.getItem("wedding_active_project")).toBeNull();
    expect(await screen.findByRole("heading", { name: "开始共同创作" })).toBeInTheDocument();
    confirm.mockRestore();
  });
});
