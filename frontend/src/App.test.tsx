import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import App from "./App";
import type { Garment, AnalysisTask } from "./types";
import purchaseContracts from "./test/purchase-contract-fixtures.json";

const garment = {
  id: "garment-1",
  source_upload_id: null,
  image_url: "/static/uploads/shirt.jpg",
  image_key: "garments/shirt.jpg",
  thumbnail_url: "/static/uploads/shirt.jpg",
  category: "top",
  colors: ["white"],
  style: "通勤",
  material: "棉",
  season: ["spring"],
  fit: "regular",
  tags: ["通勤"],
  crop_box: null,
  ai_result: { category: "top" },
  ai_confidence: 0.82,
  status: "ready",
  review_status: "confirmed",
  user_confirmed_at: "2026-05-21T00:00:00Z",
  created_at: "2026-05-21T00:00:00Z",
  updated_at: "2026-05-21T00:00:00Z"
};

const pendingGarment = {
  ...garment,
  id: "garment-pending",
  source_upload_id: "upload-1",
  image_url: "/static/uploads/crops/top.jpg",
  image_key: "garments/crops/top.jpg",
  thumbnail_url: "/static/uploads/crops/top.jpg",
  status: "ready",
  review_status: "pending_review",
  user_confirmed_at: null,
  crop_box: { x: 10, y: 20, width: 300, height: 400 }
};

const secondGarment = {
  ...garment,
  id: "garment-2",
  image_url: "/static/uploads/pants.jpg",
  image_key: "garments/pants.jpg",
  thumbnail_url: "/static/uploads/pants.jpg",
  category: "bottom",
  colors: ["black"],
  style: "休闲",
  tags: ["休闲"],
  created_at: "2026-05-22T00:00:00Z",
  updated_at: "2026-05-22T00:00:00Z"
};

const shoesGarment = {
  ...garment,
  id: "garment-3",
  image_url: "/static/uploads/shoes.jpg",
  image_key: "garments/shoes.jpg",
  thumbnail_url: "/static/uploads/shoes.jpg",
  category: "shoes",
  colors: ["black"],
  style: "通勤",
  tags: ["通勤"],
  created_at: "2026-05-23T00:00:00Z",
  updated_at: "2026-05-23T00:00:00Z"
};

const purchaseCandidate = {
  id: "candidate-1",
  product_url: "https://shop.example.com/products/black-shirt",
  source_image_url: "https://shop.example.com/black-shirt.jpg",
  image_url: "/static/uploads/purchase/black-shirt.jpg",
  image_key: "purchase/black-shirt.jpg",
  thumbnail_url: "/static/uploads/purchase/black-shirt.jpg",
  title: "Black Shirt",
  domain: "shop.example.com",
  category: "top",
  colors: ["black"],
  style: "casual",
  material: "cotton",
  season: ["summer"],
  fit: "regular",
  tags: ["T-shirt", "casual"],
  ai_result: { category: "top" },
  ai_confidence: 0.88,
  similar_items: [
    {
      garment_id: "garment-1",
      image_url: "/static/uploads/shirt.jpg",
      similarity: 86,
      matched_reasons: ["same category", "similar color"]
    }
  ],
  recommendation: "consider",
  score: 68,
  reason_summary: "已有相似黑色上衣，但夏季搭配仍有一定价值。",
  analysis: {
    conclusion: "consider",
    score: 68,
    summary: "夏季搭配空间不错，但与衣橱里的黑色上衣有重复。",
    dimensions: {
      outfit_potential: 72,
      scene_match: 66,
      gap_fill: 45,
      duplicate_risk: 86,
      idle_risk: 38
    },
    duplicate_risk: 86,
    idle_risk: 38,
    outfit_potential: 72,
    match_scenes: ["通勤", "周末"],
    suggested_price: { min: 99, ideal: 150, max: 199 },
    product_price: { current: "129.00", currency: "CNY", source: "product_page" },
    score_breakdown: {
      duplicate_risk: 86,
      wardrobe_gap: 45,
      outfit_potential: 72,
      scene_match: 66,
      idle_risk: 38
    },
    pros: ["已有下装可以直接搭配", "适合夏季高频场景"],
    cons: ["颜色和已有单品接近", "材质信息仍需确认"],
    outfit_ideas: [
      {
        scene: "周末咖啡",
        items: [
          { category: "bottom", image_url: "/static/uploads/pants.jpg", reason: "黑白配色稳定" }
        ],
        reason: "用现有黑色下装压住休闲感。"
      }
    ],
    idle_risk_detail: { level: "中", reason: "相似黑色上衣较多，可能降低穿着频率。" },
    next_actions: ["如果低于建议价再入手", "优先确认面料厚度"],
    duplicate_score: 86,
    wardrobe_gap_score: 45,
    pairing_score: 72,
    decision_factors: ["somewhat similar item already owned"],
    similar_items: [
      {
        garment_id: "garment-1",
        image_url: "/static/uploads/shirt.jpg",
        similarity: 86,
        matched_reasons: ["same category", "similar color"]
      }
    ]
  },
  status: "ready",
  created_at: "2026-06-16T00:00:00Z",
  updated_at: "2026-06-16T00:00:00Z"
};

const shoppingRun = {
  id: "shopping-run-1",
  target: "auto_gap",
  keywords: ["work skirt", "black shoes"],
  status: "ready",
  error_code: null,
  cache_hit: false,
  rate_limit: {
    remaining_refreshes: 2,
    reset_at: "2026-06-17T10:10:00Z"
  },
  wardrobe_gaps: [{ category: "shoes", label: "鞋", score: 90, reason: "当前只有 0 件。" }],
  avoid_categories: ["white上衣"],
  recommendation_groups: [
    {
      title: "优先补齐缺口",
      reason: "这些商品已经通过分析，和当前衣橱缺口的匹配度最高。",
      item_ids: ["shopping-item-1"]
    }
  ],
  items: [
    {
      id: "shopping-item-1",
      platform: "taobao",
      platform_item_id: "tb-1",
      title: "Black Work Skirt",
      image_url: "https://img.example.com/skirt.jpg",
      price: "129.00",
      shop_name: "Demo Taobao Shop",
      product_url: "https://item.taobao.com/item.htm?id=tb-1",
      analysis_status: "analyzed",
      purchase_candidate_id: "candidate-shopping-1",
      recommendation: "recommend",
      score: 82,
      reason_summary: "Fills a work bottom gap.",
      similar_items: []
    },
    {
      id: "shopping-item-2",
      platform: "taobao",
      platform_item_id: "tb-2",
      title: "Neutral Low Heel Shoes",
      image_url: "https://img.example.com/shoes.jpg",
      price: "169.00",
      shop_name: "Demo Taobao Shop",
      product_url: "https://item.taobao.com/item.htm?id=tb-2",
      analysis_status: "pending_analysis",
      purchase_candidate_id: null,
      recommendation: null,
      score: null,
      reason_summary: "",
      similar_items: []
    }
  ],
  created_at: "2026-06-17T10:00:00Z",
  updated_at: "2026-06-17T10:00:00Z"
};

const savedShoppingGarment = {
  ...garment,
  id: "saved-shopping",
  image_url: "/static/uploads/purchase/skirt.jpg",
  image_key: "purchase/skirt.jpg",
  thumbnail_url: "/static/uploads/purchase/skirt.jpg",
  category: "bottom",
  colors: ["black"],
  created_at: "2026-06-17T10:00:00Z",
  updated_at: "2026-06-17T10:00:00Z"
};

const wardrobeReport = {
  total: 2,
  ready_total: 1,
  summary: "最值得补充的是鞋。",
  category_distribution: [{ key: "top", label: "上衣", count: 1, ratio: 1 }],
  color_distribution: [{ key: "white", label: "white", count: 1, ratio: 1 }],
  style_distribution: [{ key: "通勤", label: "通勤", count: 1, ratio: 1 }],
  scene_coverage: { work: 20, casual: 20, sport: 0, date: 0 },
  duplicate_risks: [
    {
      category: "top",
      label: "white上衣",
      colors: ["white"],
      count: 2,
      garment_ids: ["garment-1", "garment-2"]
    }
  ],
  low_use_items: [
    {
      garment_id: "garment-1",
      category: "top",
      label: "白色上衣",
      reason: "近期搭配频率偏低",
      score: 35
    }
  ],
  wardrobe_gaps: [{ category: "shoes", label: "鞋", score: 90, reason: "当前只有 0 件。" }],
  avoid_categories: ["white上衣"],
  suggested_categories: ["鞋"]
};

function mockFetchOnce(body: unknown, status = 200) {
  vi.mocked(fetch).mockResolvedValueOnce({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body
  } as Response);
}

function mockDurablePurchaseApi(candidate: unknown = purchaseCandidate, failUrl = false, restored = false) {
  const result = { ...purchaseCandidate, asset_id: "asset-1", analysis_source: "remote", actual_price: null, currency: null,
    decision_status: "considering", analysis_status: "ready", ownership_state: "never_saved", owned_garment_id: null,
    revision: 1, snapshot_revision: 1, stale: false, coverage: {}, evidence: {}, ...(candidate as object) };
  const task: AnalysisTask = { ...taskRecord("purchase-task", "garment_single", "succeeded"), kind: "purchase_url", result_refs: [{ resource_type: "purchase_candidate", id: purchaseCandidate.id }] };
  let currentTask = task;
  vi.mocked(fetch).mockImplementation(async (input, init) => {
    const path = String(input);
    if (path.startsWith("/v2/garments?")) return new Response(JSON.stringify({ items: [garment], has_more: false, next_cursor: null }));
    if (path === "/v2/tasks?limit=50") return new Response(JSON.stringify({ items: restored ? [task] : [], has_more: false, next_cursor: null }));
    if (path === "/v2/tasks/purchase-analysis") {
      const image = init?.body instanceof FormData;
      currentTask = failUrl && !image ? { ...task, status: "failed", error: { code: "product_image_not_found", retryable: false }, result_refs: [] } : { ...task, kind: image ? "purchase_image" : "purchase_url" };
      return new Response(JSON.stringify(currentTask), { status: 202 });
    }
    if (path === "/v2/tasks/purchase-task") return new Response(JSON.stringify(currentTask));
    if (path === `/v2/purchase/candidates/${purchaseCandidate.id}`) return new Response(JSON.stringify(result));
    if (path.endsWith("/save")) return new Response(JSON.stringify({ garment_id: "saved-purchase", candidate_id: purchaseCandidate.id, ownership_state: "linked", revision: 2 }), { status: 201 });
    if (path === "/v2/garments/saved-purchase") return new Response(JSON.stringify({ ...garment, id: "saved-purchase" }));
    // Also exercise nullable rendering against the pre-fix direct path during RED.
    if (path === "/purchase/analyze" || path === "/purchase/analyze-image") return new Response(JSON.stringify(result), { status: 201 });
    return new Response(JSON.stringify({ detail: { code: "unexpected_test_request", path } }), { status: 500 });
  });
}

function taskRecord(id: string, kind: "garment_single" | "garment_split", status: "queued" | "succeeded", resultIds: string[] = []): AnalysisTask {
  return {
    id,
    kind,
    status,
    stage: status === "succeeded" ? "persisting" : "validating",
    submission_status: status === "succeeded" ? "completed" : "not_submitted",
    cancel_requested: false,
    action_required: false,
    attempt_count: 1,
    error: null,
    result_refs: resultIds.map((id) => ({ resource_type: "garment", id })),
    poll_after_seconds: status === "succeeded" ? null : 1,
    created_at: "2026-05-21T00:00:00Z",
    updated_at: "2026-05-21T00:00:01Z"
  };
}

function mockTaskUploadApi(results: Array<Array<Record<string, unknown> & { id: string }>>) {
  let nextTask = 0;
  const tasks = new Map<string, ReturnType<typeof taskRecord>>();
  const garments = new Map(results.flat().map((item) => [item.id, item]));
  vi.mocked(fetch).mockImplementation(async (input, init) => {
    const url = String(input);
    if (url === "/v2/tasks?limit=50") return new Response(JSON.stringify({ items: [], next_cursor: null, has_more: false }));
    if (url === "/v2/tasks/garment-upload") {
      const index = nextTask++;
      const kind = (init?.body as FormData).get("mode") === "split" ? "garment_split" : "garment_single";
      const resultIds = (results[index] || []).map((item) => item.id);
      const created = taskRecord(`task-${index}`, kind, "queued");
      tasks.set(created.id, taskRecord(created.id, kind, "succeeded", resultIds));
      return new Response(JSON.stringify(created), { status: 202 });
    }
    const taskMatch = url.match(/^\/v2\/tasks\/(task-\d+)$/);
    if (taskMatch) return new Response(JSON.stringify(tasks.get(taskMatch[1])));
    const garmentMatch = url.match(/^\/v2\/garments\/([^/?]+)$/);
    if (garmentMatch) return new Response(JSON.stringify(garments.get(garmentMatch[1]) || {}));
    return new Response(JSON.stringify({ detail: { code: "unexpected_test_request", path: url } }), { status: 500 });
  });
}

function jsonResponse(body: unknown, status = 200) {
  return { ok: status >= 200 && status < 300, status, json: async () => body, blob: async () => new Blob() } as Response;
}

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  vi.stubGlobal("fetch", vi.fn());
  vi.stubGlobal("navigator", {
    ...navigator,
    geolocation: {
      getCurrentPosition: vi.fn((success) => success({ coords: { latitude: 31.2304, longitude: 121.4737 } }))
    }
  });
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  window.history.replaceState({}, "", "/");
});

describe("AiWardrobe app", () => {
  test("renders distinct purchase outfits sharing a scene without duplicate React keys", async () => {
    const user = userEvent.setup();
    const errors = vi.spyOn(console, "error").mockImplementation(() => {});
    localStorage.setItem("aiwardrobe_token", "token");
    mockDurablePurchaseApi({ ...purchaseCandidate, analysis: { ...purchaseCandidate.analysis, outfit_ideas: [
      { scene: "周末", reason: "组合一", items: [{ candidate_id: "candidate-1", category: "top", reason: "候选" }, { garment_id: "bottom-1", category: "bottom", reason: "下装一" }] },
      { scene: "周末", reason: "组合二", items: [{ candidate_id: "candidate-1", category: "top", reason: "候选" }, { garment_id: "bottom-2", category: "bottom", reason: "下装二" }] },
      { scene: "周末", reason: "旧快照组合", items: [{ category: "shoes", image_url: "/legacy-shoes.jpg", reason: "旧鞋子" }] }
    ] } });
    try {
      render(<App />);
      await user.click(await screen.findByRole("button", { name: "上传商品图" }));
      await user.upload(await screen.findByLabelText("上传商品图片"), new File(["image"], "product.jpg", { type: "image/jpeg" }));
      expect(await screen.findByText("组合一")).toBeInTheDocument();
      expect(screen.getByText("组合二")).toBeInTheDocument();
      expect(screen.getByText("旧快照组合")).toBeInTheDocument();
      expect(errors.mock.calls.filter((args) => args.some((value) => String(value).includes("same key")))).toHaveLength(0);
    } finally { errors.mockRestore(); }
  });

  test.each(Object.entries(purchaseContracts))("renders backend nullable %s purchase contract through the default durable entry", async (scenario, contract) => {
    const user = userEvent.setup();
    localStorage.setItem("aiwardrobe_token", "token");
    mockDurablePurchaseApi(contract);
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "粘贴商品链接" }));
    await user.type(screen.getByLabelText("商品链接"), purchaseCandidate.product_url);
    await user.click(screen.getByRole("button", { name: "开始分析" }));
    expect(await screen.findByRole("heading", { name: "价格建议" })).toBeInTheDocument();
    expect(screen.queryByText(/建议价 ¥/)).not.toBeInTheDocument();
    expect(screen.getAllByText(/依据不足/).length).toBeGreaterThan(0);
    if (contract.score === null) {
      expect(screen.getByText(/暂不生成总分/)).toBeInTheDocument();
      expect(screen.getByText("重复风险 依据不足")).toBeInTheDocument();
      expect(screen.queryByText("重复风险 0")).not.toBeInTheDocument();
    }
    if (scenario === "hard_constraint") expect(screen.getAllByText(/超过你的预算/).length).toBeGreaterThan(0);
    if (scenario === "unknown_price") expect(screen.getByText("价格未知，暂不能比较预算")).toBeInTheDocument();
    const creation = vi.mocked(fetch).mock.calls.find(([input]) => String(input) === "/v2/tasks/purchase-analysis");
    expect(creation).toBeDefined();
    expect(new Headers(creation?.[1]?.headers).get("Idempotency-Key")).toMatch(/^[a-f0-9-]{36}$/i);
    expect(vi.mocked(fetch).mock.calls.some(([input]) => String(input).startsWith("/purchase/analyze"))).toBe(false);
  });

  test("ready pending-review and unconfirmed legacy garments require confirmation and never appear in outfit selectors", async () => {
    const user = userEvent.setup();
    localStorage.setItem("aiwardrobe_token", "token");
    const legacy = { ...garment, id: "legacy-1", style: "旧资料", user_confirmed_at: null };
    const pending = { ...pendingGarment, style: "待确认款" };
    vi.mocked(fetch).mockImplementation(async (input) => {
      const path = String(input);
      if (path.startsWith("/v2/garments?")) return new Response(JSON.stringify({ items: [garment, pending, legacy], has_more: false, next_cursor: null }));
      if (path === "/v2/garments/garment-pending/confirm") return new Response(JSON.stringify({ id: pending.id, status: "ready", review_status: "confirmed", tags: pending.tags }));
      if (path === "/v2/garments/garment-pending") return new Response(JSON.stringify({ ...pending, review_status: "confirmed", user_confirmed_at: "2026-10-01T00:00:00Z" }));
      if (path.startsWith("/weather")) return new Response(JSON.stringify({ temperature: 22, condition: "Cloudy", city: "当前位置" }));
      return new Response(JSON.stringify({ items: [], has_more: false, next_cursor: null }));
    });
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "衣橱" }));
    expect(await screen.findByText("待确认单品")).toBeInTheDocument();
    expect(within(screen.getByRole("button", { name: "编辑上衣 待确认款" })).getByText("待确认")).toBeInTheDocument();
    expect(within(screen.getByRole("button", { name: "编辑上衣 旧资料" })).getByText("待确认")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "搭配" }));
    await user.click(screen.getByRole("checkbox", { name: "想把某件衣服搭出来" }));
    expect(within(screen.getByLabelText("指定单品")).getAllByRole("option")).toHaveLength(1);
    await user.click(screen.getByRole("button", { name: "自己搭配" }));
    expect(screen.queryByRole("checkbox", { name: /待确认款|旧资料/ })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "衣橱" }));
    await user.click(screen.getByRole("button", { name: "编辑上衣 待确认款" }));
    await user.click(await screen.findByRole("button", { name: "确认并加入衣橱" }));
    expect(await screen.findByText("我的衣橱")).toBeInTheDocument();
    expect(vi.mocked(fetch).mock.calls.some(([input]) => String(input) === "/v2/garments/garment-pending/confirm")).toBe(true);
  });

  test("opening default purchase entry restores existing successful task result", async () => {
    const user = userEvent.setup();
    localStorage.setItem("aiwardrobe_token", "token");
    mockDurablePurchaseApi(purchaseContracts.insufficient, false, true);
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "粘贴商品链接" }));
    expect(await screen.findByRole("heading", { name: "购买结论" })).toBeInTheDocument();
    expect(screen.getByText(/暂不生成总分/)).toBeInTheDocument();
  });

  test("logged-in app opens on the buy-before home screen", async () => {
    localStorage.setItem("aiwardrobe_token", "token");
    mockFetchOnce({ items: [garment, secondGarment] });

    render(<App />);

    expect(await screen.findByRole("heading", { name: "买衣服前，先让 AI 帮你看看值不值得买" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "上传商品图" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "粘贴商品链接" })).toBeInTheDocument();
  });

  test("main navigation includes wedding studio alongside core entries", async () => {
    const user = userEvent.setup();
    localStorage.setItem("aiwardrobe_token", "token");
    mockFetchOnce({ items: [] });

    render(<App />);

    await screen.findByRole("heading", { name: "买衣服前，先让 AI 帮你看看值不值得买" });

    for (const label of ["首页", "衣橱", "搭配", "报告", "历史"]) {
      expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
    }
    expect(screen.getByRole("button", { name: "婚礼 MV" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "AI 换装" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "衣橱" }));
    const wardrobeHeading = await screen.findByRole("heading", { name: "我的衣橱" });
    await waitFor(() => expect(document.activeElement).toBe(wardrobeHeading));
    expect(screen.getByRole("button", { name: "衣橱" })).toHaveAttribute("aria-current", "page");
    await userEvent.setup().click(screen.getByRole("button", { name: "婚礼 MV" }));
    expect(await screen.findByRole("heading", { name: "开始共同创作" })).toBeInTheDocument();
  });

  test.each(["?", "#"])("opens a bare wedding invite through %s without requiring wardrobe login", async (separator) => {
    const user = userEvent.setup();
    const inviteToken = "one-use-invite";
    const serverProjectId = "server-project-id";
    const project = {
      id: serverProjectId, title: "新人项目", status: "draft", version: 1, style: "warm_documentary",
      target_duration_ms: 90000, aspect_ratio: "16:9", budget_limit_fen: 10000,
      budget_reserved_fen: 0, budget_spent_fen: 0, created_at: "2026-10-01T00:00:00Z",
      updated_at: "2026-10-01T00:00:00Z", participants: [{ person_key: "B", display_name: "小林", consent_status: "active", confirmed_at: "2026-10-01T00:00:00Z" }], story_text: null
    };
    vi.mocked(fetch).mockImplementation(async (input) => {
      const path = String(input);
      if (path === "/consent-invites/preview") return jsonResponse({project_id:serverProjectId,person_key:"B",display_name:"小林",kind:"consent",terms_version:"portrait-v1",provider_list:["wan_r2v"],scope:["wedding"],purposes:["Create wedding film"],deletion:"Delete on withdrawal",expires_at:"2027-01-01T00:00:00Z",disclosure_hash:"a".repeat(64)});
      if (path === "/consent-invites/confirm") return jsonResponse({ accepted: true, access_token: "participant-only-token", project_id: serverProjectId, participant: { person_key: "B", display_name: "小林", consent_status: "active", confirmed_at: "2026-10-01T00:00:00Z" } });
      if (path === "/wedding-capabilities") return jsonResponse({ mode: "demo", demo_only: true, providers: {}, ffmpeg_available: false, licensed_music_available: false, real_export_available: false });
      if (path === `/wedding-projects/${serverProjectId}`) return jsonResponse(project);
      if (path.endsWith("/assets") || path.endsWith("/generation-jobs") || path.endsWith("/candidates") || path.endsWith("/renders")) return jsonResponse([]);
      if (path.endsWith("/storyboard")) return jsonResponse({ detail: "STORYBOARD_NOT_APPROVED" }, 409);
      return jsonResponse({ detail: "unexpected request" }, 404);
    });
    window.history.replaceState({}, "", `/${separator}wedding_project=untrusted-query-id&wedding_invite=${inviteToken}`);

    render(<App />);

    expect(await screen.findByRole("heading", { name: "一起制作你们的婚礼 MV" })).toBeInTheDocument();
    expect(screen.getByLabelText("你的称呼")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /欢迎回来|创建你的账号/ })).not.toBeInTheDocument();
    await user.type(screen.getByLabelText("你的称呼"), "小林");
    await user.click(screen.getByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: "接受邀请并继续" }));
    expect(await screen.findByText("新人项目")).toBeInTheDocument();
    expect(sessionStorage.getItem(`wedding_participant:${serverProjectId}`)).toBe("participant-only-token");
    expect(sessionStorage.getItem("wedding_participant:untrusted-query-id")).toBeNull();
    expect(localStorage.getItem("aiwardrobe_token")).toBeNull();
    expect(vi.mocked(fetch).mock.calls.every(([url])=>!String(url).includes(inviteToken))).toBe(true);
    expect(window.location.href).not.toContain(inviteToken);
    window.history.replaceState({}, "", "/");
  });

  test("restores the participant project through explicit role selection without replacing the wardrobe JWT", async () => {
    const user = userEvent.setup();
    const projectId = "project-session-1";
    const participantToken = "wedding-session-token";
    localStorage.setItem("aiwardrobe_token", "wardrobe-jwt-stays");
    sessionStorage.setItem("wedding_active_project", projectId);
    sessionStorage.setItem(`wedding_participant:${projectId}`, participantToken);
    const project = {
      id: projectId, title: "夏日婚礼", status: "draft", version: 1, style: "warm_documentary",
      target_duration_ms: 90000, aspect_ratio: "16:9", budget_limit_fen: 10000,
      budget_reserved_fen: 0, budget_spent_fen: 0, created_at: "2026-10-01T00:00:00Z",
      updated_at: "2026-10-01T00:00:00Z", participants: [{ person_key: "B", display_name: "小林", consent_status: "active", confirmed_at: "2026-10-01T00:00:00Z" }], story_text: null
    };
    vi.mocked(fetch).mockImplementation(async (input) => {
      const path = String(input);
      if (path === "/garments") return jsonResponse({ items: [] });
      if (path === "/wedding-capabilities") return jsonResponse({ mode: "demo", demo_only: true, providers: {}, ffmpeg_available: false, licensed_music_available: false, real_export_available: false });
      if (path === `/wedding-projects/${projectId}`) return jsonResponse(project);
      if (path.endsWith("/assets") || path.endsWith("/generation-jobs") || path.endsWith("/candidates") || path.endsWith("/renders")) return jsonResponse([]);
      if (path.endsWith("/storyboard")) return jsonResponse({ detail: "STORYBOARD_NOT_APPROVED" }, 409);
      return jsonResponse({ detail: "unexpected request" }, 404);
    });

    render(<App />);

    expect(await screen.findByText("夏日婚礼")).toBeInTheDocument();
    await user.click(screen.getByRole("button", {name:"切换 B 方本人"}));
    await waitFor(() => expect(vi.mocked(fetch).mock.calls.some(([, init]) => new Headers(init?.headers).get("Authorization") === `Bearer ${participantToken}`)).toBe(true));
    expect(localStorage.getItem("aiwardrobe_token")).toBe("wardrobe-jwt-stays");
    expect(sessionStorage.getItem(`wedding_participant:${projectId}`)).toBe(participantToken);
  });

  test("shows a skippable preference panel after three ready garments and saves it", async () => {
    const user = userEvent.setup();
    localStorage.setItem("aiwardrobe_token", "token");
    mockFetchOnce({ items: [garment, secondGarment, shoesGarment] });
    mockFetchOnce({
      primary_goal: "",
      scenes: [],
      styles: [],
      avoid_types: [],
      budget_range: ""
    });
    mockFetchOnce({
      primary_goal: "少买闲置",
      scenes: ["通勤"],
      styles: ["简约"],
      avoid_types: ["低质感针织"],
      budget_range: "100-300"
    });

    render(<App />);

    expect(await screen.findByText("个性化偏好")).toBeInTheDocument();
    await user.type(screen.getByLabelText("主要目标"), "少买闲置");
    await user.type(screen.getByLabelText("常用场景"), "通勤");
    await user.type(screen.getByLabelText("喜欢风格"), "简约");
    await user.type(screen.getByLabelText("避雷类型"), "低质感针织");
    await user.type(screen.getByLabelText("预算区间"), "100-300");
    await user.click(screen.getByRole("button", { name: "保存偏好" }));

    await waitFor(() => expect(String(vi.mocked(fetch).mock.calls[2][0])).toBe("/preferences/me"));
    expect(vi.mocked(fetch).mock.calls[2][1]).toEqual(expect.objectContaining({ method: "PUT" }));
    const body = JSON.parse(String(vi.mocked(fetch).mock.calls[2][1]?.body));
    expect(body.scenes).toEqual(["通勤"]);
    expect(screen.queryByText("个性化偏好")).not.toBeInTheDocument();
  });

  test("home product image CTA opens purchase analysis with upload immediately available", async () => {
    const user = userEvent.setup();
    localStorage.setItem("aiwardrobe_token", "token");
    mockFetchOnce({ items: [] });

    render(<App />);

    await screen.findByRole("heading", { name: "买衣服前，先让 AI 帮你看看值不值得买" });
    await user.click(screen.getByRole("button", { name: "上传商品图" }));

    expect(await screen.findByLabelText("上传商品图片")).toBeInTheDocument();
    expect(screen.getByLabelText("商品链接")).toBeInTheDocument();
  });

  test("registers with email and password and shows the empty wardrobe state", async () => {
    const user = userEvent.setup();
    mockFetchOnce({
      access_token: "token",
      token_type: "bearer",
      user: { id: "user-1", email: "user@example.com", created_at: "2026-05-21T00:00:00Z", updated_at: "2026-05-21T00:00:00Z" }
    });
    mockFetchOnce({ items: [] });

    render(<App />);

    expect(screen.getByRole("button", { name: "注册账号" })).toBeDisabled();
    await user.type(screen.getByLabelText("邮箱"), "user@example.com");
    await user.type(screen.getByLabelText("密码"), "correct-horse-123");
    await user.click(screen.getByRole("button", { name: "注册账号" }));

    expect(await screen.findByRole("heading", { name: "买衣服前，先让 AI 帮你看看值不值得买" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "衣橱" }));
    expect(await screen.findByText("还没有衣服")).toBeInTheDocument();
    expect(screen.getByText("上传几件常穿单品，AI 才能开始帮你搭配。")).toBeInTheDocument();
  });

  test("clears stale session when the backend rejects the bearer token", async () => {
    localStorage.setItem("aiwardrobe_token", "stale-token");
    mockFetchOnce({ detail: "Invalid bearer token" }, 401);

    render(<App />);

    expect(await screen.findByRole("button", { name: "注册账号" })).toBeInTheDocument();
    expect(localStorage.getItem("aiwardrobe_token")).toBeNull();
  });

  test("uploading a photo shows extracted pending-review garments", async () => {
    const user = userEvent.setup();
    localStorage.setItem("aiwardrobe_token", "token");
    mockFetchOnce({ items: [] });
    mockTaskUploadApi([[pendingGarment, secondGarment, { ...garment, id: "garment-shoes", category: "shoes", image_url: "/v2/assets/shoes/content", thumbnail_url: "/v2/assets/shoes/content", image_key: "", style: "运动" }]]);

    render(<App />);

    await screen.findByRole("heading", { name: "买衣服前，先让 AI 帮你看看值不值得买" });
    await user.click(screen.getByRole("button", { name: "衣橱" }));
    await screen.findByText("我的衣橱");
    await user.click(screen.getByRole("button", { name: "上传衣服" }));
    await user.click(screen.getByRole("button", { name: "自动识别" }));
    const file = new File(["fake image"], "multi-look.jpg", { type: "image/jpeg" });
    await user.upload(screen.getByLabelText("选择整套或多单品照片"), file);

    expect(await screen.findByText("单品拆分完成，待确认")).toBeInTheDocument();
    expect(screen.getByText("识别结果")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /编辑上衣/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /编辑下装/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /编辑鞋子/ })).toBeInTheDocument();
    expect(screen.queryByAltText("multi-look.jpg 原图")).not.toBeInTheDocument();
    const uploadCall = vi.mocked(fetch).mock.calls.find(([url]) => String(url) === "/v2/tasks/garment-upload");
    expect(uploadCall).toBeDefined();
    expect(new Headers(uploadCall?.[1]?.headers).get("Idempotency-Key")).toBeTruthy();
    expect((uploadCall?.[1]?.body as FormData).get("mode")).toBe("split");
  });

  test("preserves a guest purchase link through registration", async () => {
    const user = userEvent.setup();
    mockFetchOnce({
      access_token: "token",
      token_type: "bearer",
      user: { id: "user-1", email: "guest@example.com", created_at: "2026-05-21T00:00:00Z", updated_at: "2026-05-21T00:00:00Z" }
    });
    mockFetchOnce({ items: [] });
    render(<App />);

    const productUrl = "https://shop.example.com/products/summer-shirt";
    await user.type(screen.getByLabelText("填写商品链接，登录后继续"), productUrl);
    await user.type(screen.getByLabelText("邮箱"), "guest@example.com");
    await user.type(screen.getByLabelText("密码"), "correct-horse-123");
    await user.click(screen.getByRole("button", { name: "注册账号" }));

    expect(await screen.findByRole("heading", { name: "购买分析" })).toBeInTheDocument();
    expect(screen.getByLabelText("商品链接")).toHaveValue(productUrl);
    expect(screen.getByText("已恢复登录前填写的商品链接，确认后可继续分析。")).toBeInTheDocument();
  });

  test("plain upload creates a ready tagged garment without auto split workflow", async () => {
    const user = userEvent.setup();
    localStorage.setItem("aiwardrobe_token", "token");
    mockFetchOnce({ items: [] });
    mockTaskUploadApi([[{
      ...pendingGarment,
      id: "plain-1",
      source_upload_id: null,
      image_url: "/v2/assets/plain/content",
      image_key: "",
      thumbnail_url: "/v2/assets/plain/content",
      crop_box: null,
      ai_result: { source: "plain_upload" }
    }]]);

    render(<App />);

    await screen.findByRole("heading", { name: "买衣服前，先让 AI 帮你看看值不值得买" });
    await user.click(screen.getByRole("button", { name: "衣橱" }));
    await screen.findByText("我的衣橱");
    await user.click(screen.getByRole("button", { name: "上传衣服" }));
    expect(screen.getByText(/每张图片作为一件衣物识别/)).toBeInTheDocument();
    const file = new File(["fake image"], "single-shirt.jpg", { type: "image/jpeg" });
    await user.upload(screen.getByLabelText("选择单件图片"), file);

    expect(await screen.findByText("识别完成，待确认")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /编辑上衣/ })).toBeInTheDocument();
    const uploadCall = vi.mocked(fetch).mock.calls.find(([url]) => String(url) === "/v2/tasks/garment-upload");
    expect((uploadCall?.[1]?.body as FormData).get("mode")).toBe("single");
  });

  test("plain multi-file upload creates independent recoverable tasks", async () => {
    const user = userEvent.setup();
    localStorage.setItem("aiwardrobe_token", "token");
    mockFetchOnce({ items: [] });
    mockTaskUploadApi([[garment], [secondGarment]]);

    render(<App />);

    await screen.findByRole("heading", { name: "买衣服前，先让 AI 帮你看看值不值得买" });
    await user.click(screen.getByRole("button", { name: "衣橱" }));
    await screen.findByText("我的衣橱");
    await user.click(screen.getByRole("button", { name: "上传衣服" }));
    expect(screen.getByText(/上传 3 件常穿衣物即可提升分析准确度/)).toBeInTheDocument();

    const files = [
      new File(["shirt image"], "single-shirt.jpg", { type: "image/jpeg" }),
      new File(["pants image"], "single-pants.jpg", { type: "image/jpeg" })
    ];
    await user.upload(screen.getByLabelText("选择单件图片"), files);

    await waitFor(() => {
      expect(vi.mocked(fetch).mock.calls.filter(([url]) => String(url) === "/v2/tasks/garment-upload")).toHaveLength(2);
    }, { timeout: 5000 });
    expect(await screen.findAllByText("识别完成，待确认")).toHaveLength(2);
    expect(screen.getByRole("button", { name: /编辑上衣/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /编辑下装/ })).toBeInTheDocument();
    const uploadCalls = vi.mocked(fetch).mock.calls.filter(([url]) => String(url) === "/v2/tasks/garment-upload");
    expect(uploadCalls).toHaveLength(2);
    expect(uploadCalls.every(([, options]) => new Headers(options?.headers).has("Idempotency-Key"))).toBe(true);
  });

  test("clears stale session when auto upload rejects the bearer token", async () => {
    const user = userEvent.setup();
    localStorage.setItem("aiwardrobe_token", "stale-token");
    mockFetchOnce({ items: [] });
    mockFetchOnce({ items: [] });
    mockFetchOnce({ detail: "Invalid bearer token" }, 401);

    render(<App />);

    await screen.findByRole("heading", { name: "买衣服前，先让 AI 帮你看看值不值得买" });
    await user.click(screen.getByRole("button", { name: "衣橱" }));
    await screen.findByText("我的衣橱");
    await user.click(screen.getByRole("button", { name: "上传衣服" }));
    await user.click(screen.getByRole("button", { name: "自动识别" }));

    const file = new File(["fake image"], "look.jpg", { type: "image/jpeg" });
    await user.upload(screen.getByLabelText("选择整套或多单品照片"), file);

    expect(await screen.findByRole("button", { name: "注册账号" })).toBeInTheDocument();
    expect(localStorage.getItem("aiwardrobe_token")).toBeNull();
  });

  test("deletes a garment from the detail page", async () => {
    const user = userEvent.setup();
    localStorage.setItem("aiwardrobe_token", "token");
    vi.stubGlobal("confirm", vi.fn(() => true));
    mockFetchOnce({ items: [garment] });
    mockFetchOnce({}, 204);

    render(<App />);

    await screen.findByRole("heading", { name: "买衣服前，先让 AI 帮你看看值不值得买" });
    await user.click(screen.getByRole("button", { name: "衣橱" }));
    await screen.findByText("我的衣橱");
    await user.click(screen.getByText(/white/));
    await user.click(screen.getByRole("button", { name: "删除衣服" }));

    await waitFor(() => {
      expect(fetch).toHaveBeenLastCalledWith("/garments/garment-1", expect.objectContaining({
        method: "DELETE",
        headers: expect.any(Headers)
      }));
    });
    expect(await screen.findByText("还没有衣服")).toBeInTheDocument();
  });

  test("batch deletes selected garments from the wardrobe", async () => {
    const user = userEvent.setup();
    localStorage.setItem("aiwardrobe_token", "token");
    vi.stubGlobal("confirm", vi.fn(() => true));
    mockFetchOnce({ items: [garment, secondGarment] });
    mockFetchOnce({}, 204);
    mockFetchOnce({}, 204);

    render(<App />);

    await screen.findByRole("heading", { name: "买衣服前，先让 AI 帮你看看值不值得买" });
    await user.click(screen.getByRole("button", { name: "衣橱" }));
    await screen.findByText("我的衣橱");
    await user.click(screen.getByRole("button", { name: "批量管理" }));
    await user.click(screen.getByRole("checkbox", { name: /选择.*top/i }));
    await user.click(screen.getByRole("checkbox", { name: /选择.*bottom/i }));
    await user.click(screen.getByRole("button", { name: "删除所选" }));

    await waitFor(() => expect(screen.queryByText(/white/)).not.toBeInTheDocument());
    expect(screen.queryByText(/black/)).not.toBeInTheDocument();
    expect(String(vi.mocked(fetch).mock.calls[1][0])).toBe("/garments/garment-1");
    expect(String(vi.mocked(fetch).mock.calls[2][0])).toBe("/garments/garment-2");
    expect(vi.mocked(fetch).mock.calls[1][1]).toEqual(expect.objectContaining({ method: "DELETE" }));
    expect(vi.mocked(fetch).mock.calls[2][1]).toEqual(expect.objectContaining({ method: "DELETE" }));
  });

  test("partial batch delete reports each failed item and removes successful items", async () => {
    const user = userEvent.setup();
    localStorage.setItem("aiwardrobe_token", "token");
    vi.stubGlobal("confirm", vi.fn(() => true));
    const items = [
      { ...garment, id: "top-1", style: "top-one" },
      { ...secondGarment, id: "bottom-1", style: "bottom-one" },
      { ...shoesGarment, id: "shoes-1", style: "shoes-one" },
      { ...garment, id: "top-2", style: "top-two" },
      { ...secondGarment, id: "bottom-2", style: "bottom-two" }
    ];
    const deleteOutcomes: Record<string, { status: number; body: unknown }> = {
      "/garments/top-1": { status: 204, body: {} },
      "/garments/bottom-1": { status: 403, body: { detail: { code: "delete_denied" } } },
      "/garments/shoes-1": { status: 204, body: {} },
      "/garments/top-2": { status: 503, body: { detail: { code: "delete_unavailable" } } },
      "/garments/bottom-2": { status: 204, body: {} }
    };
    vi.mocked(fetch).mockImplementation(async (input) => {
      const path = String(input);
      if (path === "/v2/garments?limit=50") return new Response(JSON.stringify({ items, next_cursor: null, has_more: false }));
      const outcome = deleteOutcomes[path];
      if (!outcome) return new Response(JSON.stringify({ detail: { code: "unexpected_request" } }), { status: 500 });
      return {
        ok: outcome.status >= 200 && outcome.status < 300,
        status: outcome.status,
        json: async () => outcome.body
      } as Response;
    });

    render(<App />);
    await screen.findByRole("heading", { name: "买衣服前，先让 AI 帮你看看值不值得买" });
    await user.click(screen.getByRole("button", { name: "衣橱" }));
    await screen.findByText("我的衣橱");
    await user.click(screen.getByRole("button", { name: "批量管理" }));
    for (const item of items) {
      await user.click(screen.getByRole("checkbox", { name: `选择 ${item.category} ${item.id}` }));
    }
    await user.click(screen.getByRole("button", { name: "删除所选" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("2 件衣物删除失败");
    expect(screen.getByRole("alert")).toHaveTextContent("下装 bottom-one：delete_denied");
    expect(screen.getByRole("alert")).toHaveTextContent("上衣 top-two：delete_unavailable");
    expect(screen.getAllByRole("checkbox", { name: /选择/ })).toHaveLength(2);
    expect(screen.getByRole("checkbox", { name: "选择 bottom bottom-1" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "选择 top top-2" })).toBeChecked();
  });

  test("wardrobe and outfit history load server cursor pages", async () => {
    const user = userEvent.setup();
    localStorage.setItem("aiwardrobe_token", "token");
    vi.mocked(fetch).mockImplementation(async (input) => {
      const path = String(input);
      if (path === "/v2/garments?limit=50") return new Response(JSON.stringify({ items: [garment], next_cursor: "garment-next", has_more: true }));
      if (path === "/v2/garments?cursor=garment-next&limit=50") return new Response(JSON.stringify({ items: [secondGarment], next_cursor: null, has_more: false }));
      if (path === "/v2/garments?q=white&limit=50") return new Response(JSON.stringify({ items: [garment], next_cursor: null, has_more: false }));
      if (path === "/v2/outfits?limit=50") return new Response(JSON.stringify({ items: [], next_cursor: "outfit-next", has_more: true }));
      if (path === "/v2/outfits?cursor=outfit-next&limit=50") return new Response(JSON.stringify({ items: [], next_cursor: null, has_more: false }));
      if (path === "/v2/wear-events?limit=100") return new Response(JSON.stringify({ items: [], next_cursor: "wear-next", has_more: true }));
      if (path === "/v2/wear-events?cursor=wear-next&limit=100") return new Response(JSON.stringify({
        items: [{ id: "wear-older", outfit_id: "outfit-old", worn_at: "2026-09-01T00:00:00Z", garment_ids: ["garment-1"], garment_count: 1 }],
        next_cursor: null,
        has_more: false
      }));
      return new Response(JSON.stringify({ detail: { code: "unexpected_request", path } }), { status: 500 });
    });

    render(<App />);
    await screen.findByRole("heading", { name: "买衣服前，先让 AI 帮你看看值不值得买" });
    await user.click(screen.getByRole("button", { name: "衣橱" }));
    await screen.findByRole("button", { name: "加载更多衣物" });
    await user.click(screen.getByRole("button", { name: "加载更多衣物" }));
    expect(await screen.findByRole("button", { name: /编辑下装/ })).toBeInTheDocument();
    await user.type(screen.getByPlaceholderText("搜索标签、颜色、材质"), "white");
    await waitFor(() => expect(vi.mocked(fetch).mock.calls.some(([input]) => String(input) === "/v2/garments?q=white&limit=50")).toBe(true));

    await user.click(screen.getByRole("button", { name: "历史" }));
    await screen.findByRole("button", { name: "加载更多搭配" });
    await user.click(screen.getByRole("button", { name: "加载更多搭配" }));
    await waitFor(() => expect(vi.mocked(fetch).mock.calls.some(([input]) => String(input) === "/v2/outfits?cursor=outfit-next&limit=50")).toBe(true));
    await user.click(screen.getByRole("button", { name: "加载更多穿着记录" }));
    await waitFor(() => expect(vi.mocked(fetch).mock.calls.some(([input]) => String(input) === "/v2/wear-events?cursor=wear-next&limit=100")).toBe(true));
    await waitFor(() => expect(screen.queryByRole("button", { name: "加载更多穿着记录" })).not.toBeInTheDocument());
  });

  test("upload view restores older analysis tasks through the server cursor", async () => {
    const user = userEvent.setup();
    localStorage.setItem("aiwardrobe_token", "token");
    vi.mocked(fetch).mockImplementation(async (input) => {
      const path = String(input);
      if (path === "/v2/garments?limit=50") return new Response(JSON.stringify({ items: [], next_cursor: null, has_more: false }));
      if (path === "/v2/tasks?limit=50") return new Response(JSON.stringify({ items: [], next_cursor: "upload-task-next", has_more: true }));
      if (path === "/v2/tasks?cursor=upload-task-next&limit=50") return new Response(JSON.stringify({
        items: [{
          id: "garment-task-older",
          kind: "garment_single",
          status: "failed",
          stage: "recognizing",
          submission_status: "failed",
          cancel_requested: false,
          action_required: false,
          attempt_count: 1,
          error: { code: "provider_unavailable", retryable: false },
          result_refs: [],
          poll_after_seconds: null,
          created_at: "2026-09-01T00:00:00Z",
          updated_at: "2026-09-01T00:00:00Z"
        }],
        next_cursor: null,
        has_more: false
      }));
      return new Response(JSON.stringify({ detail: { code: "unexpected_request", path } }), { status: 500 });
    });

    render(<App />);
    await user.click(await screen.findByRole("button", { name: "衣橱" }));
    await user.click(await screen.findByRole("button", { name: "上传衣服" }));
    await user.click(await screen.findByRole("button", { name: "加载更多历史任务" }));

    expect(await screen.findByText("已提交的图片任务")).toBeInTheDocument();
    expect(vi.mocked(fetch).mock.calls.some(([input]) => String(input) === "/v2/tasks?cursor=upload-task-next&limit=50")).toBe(true);
    expect(screen.queryByRole("button", { name: "加载更多历史任务" })).not.toBeInTheDocument();
  });

  test("outfit page fetches weather and saves a manual fixed outfit", async () => {
    const user = userEvent.setup();
    localStorage.setItem("aiwardrobe_token", "token");
    mockFetchOnce({ items: [garment] });
    mockFetchOnce({
      date: "2026-05-21",
      lat_key: "31.23",
      lon_key: "121.47",
      city: "当前位置",
      condition: "Cloudy",
      temperature: 22,
      feels_like: 22,
      precipitation: 0,
      wind_speed: 8,
      cached: false
    });
    mockFetchOnce({
      id: "manual-1",
      name: "周一通勤",
      occasion: "work",
      season: "",
      temperature: null,
      items: [{ garment_id: "garment-1", category: "top", image_url: "/static/uploads/shirt.jpg", reason: "用户手动选择" }],
      explanation: "用户保存的固定搭配",
      source: "manual",
      is_favorite: false,
      is_fixed: true,
      weather_snapshot: null,
      created_at: "2026-05-21T00:00:00Z"
    }, 201);

    render(<App />);

    await screen.findByRole("heading", { name: "买衣服前，先让 AI 帮你看看值不值得买" });
    await user.click(screen.getByRole("button", { name: "搭配" }));
    expect(await screen.findByText(/当前位置 · Cloudy/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "自己搭配" }));
    await user.click(screen.getByRole("checkbox", { name: /通勤/ }));
    await user.type(screen.getByLabelText("固定搭配名称"), "周一通勤");
    await user.click(screen.getByRole("button", { name: "保存固定搭配" }));

    expect(await screen.findByText("用户保存的固定搭配")).toBeInTheDocument();
    expect(screen.getByText("固定搭配")).toBeInTheDocument();
  });

  test("outfit page derives season from the current date instead of showing a selector", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.setSystemTime(new Date("2026-05-26T10:00:00+08:00"));
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    localStorage.setItem("aiwardrobe_token", "token");
    mockFetchOnce({ items: [garment] });
    mockFetchOnce({
      date: "2026-05-26",
      lat_key: "31.23",
      lon_key: "121.47",
      city: "当前位置",
      condition: "Cloudy",
      temperature: 31,
      feels_like: 33,
      precipitation: 0,
      wind_speed: 8,
      cached: false
    });
    mockFetchOnce({
      id: "outfit-season",
      name: "",
      occasion: "work",
      season: "summer",
      temperature: 31,
      items: [{ garment_id: "garment-1", category: "top", image_url: "/static/uploads/shirt.jpg", reason: "ok" }],
      explanation: "season aware",
      source: "ai",
      is_favorite: false,
      is_fixed: false,
      weather_snapshot: { temperature: 31 },
      created_at: "2026-05-26T00:00:00Z"
    });

    render(<App />);

    await screen.findByRole("heading", { name: "买衣服前，先让 AI 帮你看看值不值得买" });
    await user.click(screen.getByRole("button", { name: "搭配" }));

    expect(await screen.findByText("按节气自动判断")).toBeInTheDocument();
    expect(screen.queryByLabelText("季节")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "生成搭配" }));
    await waitFor(() => expect(String(vi.mocked(fetch).mock.calls[2][0])).toBe("/outfits/generate"));
    const body = JSON.parse(String(vi.mocked(fetch).mock.calls[2][1]?.body));
    expect(body.season).toBeUndefined();
  });

  test("outfit page generates around a selected garment and requires dislike reason", async () => {
    const user = userEvent.setup();
    localStorage.setItem("aiwardrobe_token", "token");
    mockFetchOnce({ items: [garment, secondGarment] });
    mockFetchOnce({
      date: "2026-05-27",
      lat_key: "31.23",
      lon_key: "121.47",
      city: "褰撳墠浣嶇疆",
      condition: "Cloudy",
      temperature: 23,
      feels_like: 23,
      precipitation: 0,
      wind_speed: 8,
      cached: false
    });
    mockFetchOnce({
      id: "outfit-targeted",
      name: "",
      occasion: "work",
      season: "spring",
      temperature: 23,
      items: [
        { garment_id: "garment-2", category: "bottom", image_url: "/static/uploads/pants.jpg", reason: "围绕指定单品" },
        { garment_id: "garment-1", category: "top", image_url: "/static/uploads/shirt.jpg", reason: "补齐上衣" }
      ],
      explanation: "围绕这件衣服生成通勤搭配",
      source: "ai",
      is_favorite: false,
      is_fixed: false,
      weather_snapshot: { temperature: 23 },
      created_at: "2026-05-27T00:00:00Z"
    });
    mockFetchOnce({ id: "feedback-1" }, 201);

    render(<App />);

    await screen.findByRole("heading", { name: "买衣服前，先让 AI 帮你看看值不值得买" });
    await user.click(screen.getByRole("button", { name: "搭配" }));
    expect(await screen.findByRole("button", { name: "生成搭配" })).toBeEnabled();

    await user.click(screen.getByRole("checkbox", { name: "想把某件衣服搭出来" }));
    await user.selectOptions(screen.getByLabelText("指定单品"), "garment-2");
    await user.click(screen.getByRole("button", { name: "生成搭配" }));

    await waitFor(() => expect(String(vi.mocked(fetch).mock.calls[2][0])).toBe("/outfits/generate"));
    const body = JSON.parse(String(vi.mocked(fetch).mock.calls[2][1]?.body));
    expect(body.garment_id).toBe("garment-2");
    expect(await screen.findByText("围绕这件衣服生成通勤搭配")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "不喜欢" }));
    expect(screen.getByText("请选择不喜欢原因")).toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText("不喜欢原因"), "颜色不合适");
    await user.click(screen.getByRole("button", { name: "不喜欢" }));
    expect(await screen.findByText("反馈已保存，会用于之后的搭配建议。")).toBeInTheDocument();
  });

  test("falls back to default city weather when geolocation is unavailable", async () => {
    const user = userEvent.setup();
    localStorage.setItem("aiwardrobe_token", "token");
    vi.stubGlobal("navigator", { ...navigator, geolocation: undefined });
    mockFetchOnce({ items: [garment] });
    mockFetchOnce({
      date: "2026-05-22",
      lat_key: "31.23",
      lon_key: "121.47",
      city: "当前位置",
      condition: "Cloudy",
      temperature: 24,
      feels_like: 24,
      precipitation: 0,
      wind_speed: 6,
      cached: false
    });

    render(<App />);

    await screen.findByRole("heading", { name: "买衣服前，先让 AI 帮你看看值不值得买" });
    await user.click(screen.getByRole("button", { name: "搭配" }));

    expect(await screen.findByText(/默认城市 · Cloudy/)).toBeInTheDocument();
    expect(String(vi.mocked(fetch).mock.calls[1][0])).toContain("/weather/today?lat=31.2304&lon=121.4737");
  });

  test("waits for current weather before generating an AI outfit", async () => {
    const user = userEvent.setup();
    localStorage.setItem("aiwardrobe_token", "token");
    let resolveWeather: (value: Response) => void = () => undefined;
    const weatherPromise = new Promise<Response>((resolve) => {
      resolveWeather = resolve;
    });
    mockFetchOnce({ items: [garment] });
    vi.mocked(fetch).mockImplementationOnce(() => weatherPromise);
    mockFetchOnce({
      id: "outfit-1",
      name: "",
      occasion: "work",
      season: "spring",
      temperature: 31,
      items: [{ garment_id: "garment-1", category: "top", image_url: "/static/uploads/shirt.jpg", reason: "ok" }],
      explanation: "weather aware",
      source: "ai",
      is_favorite: false,
      is_fixed: false,
      weather_snapshot: { temperature: 31 },
      created_at: "2026-05-21T00:00:00Z"
    });

    render(<App />);

    await screen.findByRole("heading", { name: "买衣服前，先让 AI 帮你看看值不值得买" });
    await user.click(screen.getByRole("button", { name: "搭配" }));
    const loadingButton = screen.getByRole("button", { name: "获取天气中" });
    expect(loadingButton).toBeDisabled();
    await user.click(loadingButton);
    expect(vi.mocked(fetch)).toHaveBeenCalledTimes(2);

    resolveWeather({
      ok: true,
      status: 200,
      json: async () => ({
        date: "2026-05-25",
        lat_key: "22.59",
        lon_key: "114.05",
        city: "褰撳墠浣嶇疆",
        condition: "Partly Cloudy",
        temperature: 31,
        feels_like: 36,
        precipitation: 0,
        wind_speed: 21,
        cached: true
      })
    } as Response);
    const generateButton = await screen.findByRole("button", { name: "生成搭配" });
    expect(generateButton).toBeEnabled();

    await user.click(generateButton);

    await waitFor(() => expect(String(vi.mocked(fetch).mock.calls[2][0])).toBe("/outfits/generate"));
    const body = JSON.parse(String(vi.mocked(fetch).mock.calls[2][1]?.body));
    expect(body.temperature).toBe(31);
    expect(body.weather.temperature).toBe(31);
  });

  test("deletes outfits from history and favorites", async () => {
    const user = userEvent.setup();
    localStorage.setItem("aiwardrobe_token", "token");
    mockFetchOnce({ items: [garment] });
    mockFetchOnce({
      items: [
        {
          id: "outfit-1",
          name: "周一通勤",
          occasion: "work",
          season: "spring",
          temperature: 31,
          items: [],
          explanation: "适合上班的固定搭配",
          source: "manual",
          is_favorite: true,
          is_fixed: true,
          weather_snapshot: null,
          created_at: "2026-05-21T00:00:00Z"
        }
      ]
    });
    mockFetchOnce({ items: [], next_cursor: null, has_more: false });
    mockFetchOnce({}, 204);

    render(<App />);

    await screen.findByRole("heading", { name: "买衣服前，先让 AI 帮你看看值不值得买" });
    await user.click(screen.getByRole("button", { name: "历史" }));
    expect(await screen.findByText("周一通勤 · 31°C")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "删除搭配" }));

    await waitFor(() => expect(screen.queryByText("周一通勤 · 31°C")).not.toBeInTheDocument());
    expect(screen.getByText("还没有搭配历史")).toBeInTheDocument();
    expect(String(vi.mocked(fetch).mock.calls[3][0])).toBe("/outfits/outfit-1");
    expect(vi.mocked(fetch).mock.calls[3][1]).toEqual(expect.objectContaining({ method: "DELETE" }));
  });

  test("records and revokes an outfit wear event", async () => {
    const user = userEvent.setup();
    localStorage.setItem("aiwardrobe_token", "token");
    mockFetchOnce({ items: [] });
    mockFetchOnce({ items: [{
      id: "outfit-wear-1",
      name: "周一通勤",
      occasion: "work",
      season: "spring",
      temperature: 22,
      items: [],
      explanation: "适合上班",
      source: "manual",
      is_favorite: false,
      is_fixed: false,
      weather_snapshot: null,
      created_at: "2026-05-21T00:00:00Z"
    }] });
    mockFetchOnce({ items: [], next_cursor: null, has_more: false });
    mockFetchOnce({
      id: "wear-event-1",
      local_date: new Date().toLocaleDateString("en-CA"),
      timezone: "UTC",
      outfit_id: "outfit-wear-1",
      occasion: "work",
      items: [],
      revoked_at: null,
      created_at: "2026-10-01T00:00:00Z"
    }, 201);
    mockFetchOnce({}, 204);

    render(<App />);
    await screen.findByRole("heading", { name: "买衣服前，先让 AI 帮你看看值不值得买" });
    await user.click(screen.getByRole("button", { name: "历史" }));
    await screen.findByText("周一通勤 · 22°C");
    await user.click(screen.getByRole("button", { name: "记录穿着" }));
    expect(await screen.findByRole("button", { name: "撤销穿着记录" })).toHaveAttribute("aria-pressed", "true");
    await user.click(screen.getByRole("button", { name: "撤销穿着记录" }));

    expect(await screen.findByRole("button", { name: "记录穿着" })).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByText("穿着记录已撤销")).toBeInTheDocument();
    expect(String(vi.mocked(fetch).mock.calls[3][0])).toBe("/v2/wear-events");
    expect(vi.mocked(fetch).mock.calls[3][1]).toEqual(expect.objectContaining({ method: "POST", headers: expect.any(Headers) }));
    expect(String(vi.mocked(fetch).mock.calls[4][0])).toBe("/v2/wear-events/wear-event-1");
    expect(vi.mocked(fetch).mock.calls[4][1]).toEqual(expect.objectContaining({ method: "DELETE" }));
  });

  test("purchase analysis submits a product URL and saves the candidate to wardrobe", async () => {
    const user = userEvent.setup();
    localStorage.setItem("aiwardrobe_token", "token");
    mockDurablePurchaseApi();

    render(<App />);

    await screen.findByRole("heading", { name: "买衣服前，先让 AI 帮你看看值不值得买" });
    await user.click(screen.getByRole("button", { name: "粘贴商品链接" }));
    await user.type(screen.getByLabelText("商品链接"), purchaseCandidate.product_url);
    await user.click(screen.getByRole("button", { name: "开始分析" }));

    expect(await screen.findByText("Black Shirt")).toBeInTheDocument();
    expect(screen.getByText("已有相似黑色上衣，但夏季搭配仍有一定价值。")).toBeInTheDocument();
    expect(screen.getByText("页面价格 ¥129.00")).toBeInTheDocument();
    expect(screen.getByText("86%")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "加入衣橱" }));

    await waitFor(() => expect(vi.mocked(fetch).mock.calls.some(([input]) => String(input) === "/v2/purchase/candidates/candidate-1/save")).toBe(true));
  });

  test("purchase analysis offers manual image upload when URL extraction fails", async () => {
    const user = userEvent.setup();
    localStorage.setItem("aiwardrobe_token", "token");
    mockDurablePurchaseApi(purchaseCandidate, true);

    render(<App />);

    await screen.findByRole("heading", { name: "买衣服前，先让 AI 帮你看看值不值得买" });
    await user.click(screen.getByRole("button", { name: "粘贴商品链接" }));
    await user.type(screen.getByLabelText("商品链接"), "https://shop.example.com/no-image");
    await user.click(screen.getByRole("button", { name: "开始分析" }));

    expect(await screen.findByText("未能自动找到商品图片，请上传商品图继续分析。")).toBeInTheDocument();
    const file = new File(["fake product image"], "manual.jpg", { type: "image/jpeg" });
    await user.upload(screen.getByLabelText("上传商品图片"), file);

    expect(await screen.findByText("Black Shirt")).toBeInTheDocument();
    expect(vi.mocked(fetch).mock.calls.filter(([input]) => String(input) === "/v2/tasks/purchase-analysis")).toHaveLength(2);
  });

  test("home product image upload analyzes immediately and shows the full purchase result", async () => {
    const user = userEvent.setup();
    localStorage.setItem("aiwardrobe_token", "token");
    mockDurablePurchaseApi();

    render(<App />);

    await screen.findByRole("heading", { name: "买衣服前，先让 AI 帮你看看值不值得买" });
    await user.click(screen.getByRole("button", { name: "上传商品图" }));
    const file = new File(["fake product image"], "purchase.jpg", { type: "image/jpeg" });
    await user.upload(await screen.findByLabelText("上传商品图片"), file);

    expect(await screen.findByText("Black Shirt")).toBeInTheDocument();
    for (const heading of [
      "购买结论",
      "分项评分",
      "推荐与风险理由",
      "相似衣橱单品",
      "可搭配方案",
      "闲置风险",
      "价格建议"
    ]) {
      expect(screen.getByRole("heading", { name: heading })).toBeInTheDocument();
    }
    expect(screen.getByText("建议价 ¥150")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "加入衣橱" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "再分析一件" })).toBeInTheDocument();
    expect(vi.mocked(fetch).mock.calls.some(([input, init]) => String(input) === "/v2/tasks/purchase-analysis" && init?.body instanceof FormData)).toBe(true);
  });

  test("shopping recommendations loads products and saves analyzed items", async () => {
    const user = userEvent.setup();
    localStorage.setItem("aiwardrobe_token", "token");
    mockFetchOnce({ items: [garment] });
    mockFetchOnce(wardrobeReport);
    mockFetchOnce(shoppingRun, 201);
    mockFetchOnce(savedShoppingGarment, 201);

    render(<App />);

    await screen.findByRole("heading", { name: "买衣服前，先让 AI 帮你看看值不值得买" });
    await user.click(screen.getByRole("button", { name: "报告" }));
    await user.click(screen.getByRole("button", { name: "查看衣橱缺口商品" }));
    expect(await screen.findByRole("heading", { name: "衣橱缺口" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "查看缺口" }));

    expect(await screen.findByText("Black Work Skirt")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "你的衣橱当前缺口" })).toBeInTheDocument();
    expect(screen.getByText("鞋 90%")).toBeInTheDocument();
    expect(screen.getAllByRole("heading", { name: "最近不建议再买" }).length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("white上衣").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("Fills a work bottom gap.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "加入衣橱" }));

    await waitFor(() => expect(String(vi.mocked(fetch).mock.calls[3][0])).toBe("/purchase/candidates/candidate-shopping-1/save"));
  });

  test("report page shows wardrobe gaps and avoid categories", async () => {
    const user = userEvent.setup();
    localStorage.setItem("aiwardrobe_token", "token");
    mockFetchOnce({ items: [garment] });
    mockFetchOnce(wardrobeReport);

    render(<App />);

    await screen.findByRole("button", { name: "报告" });
    await user.click(screen.getByRole("button", { name: "报告" }));

    expect(await screen.findByText("衣柜体检报告")).toBeInTheDocument();
    expect(screen.getByText("最值得补充的是鞋。")).toBeInTheDocument();
    expect(screen.getByText("1 / 2")).toBeInTheDocument();
    expect(screen.getByText("最近不建议再买")).toBeInTheDocument();
    expect(screen.getByText("white 1 (100%)")).toBeInTheDocument();
    expect(screen.getByText("white上衣")).toBeInTheDocument();
  });

  test("password login keeps an invalid session out and allows a corrected retry", async () => {
    const user = userEvent.setup();
    let attempts = 0;
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      if (String(input) === "/auth/login") {
        attempts += 1;
        expect(init?.method).toBe("POST");
        return jsonResponse(attempts === 1 ? { detail: "邮箱或密码错误" } : { access_token: "new-session" }, attempts === 1 ? 401 : 200);
      }
      return jsonResponse({ items: [], has_more: false, next_cursor: null });
    });
    render(<App />);
    await user.click(screen.getByRole("button", { name: "登录" }));
    expect(screen.getByLabelText("密码")).toHaveAttribute("autoComplete", "current-password");
    expect(screen.getByRole("button", { name: "登录账号" })).toBeDisabled();
    await user.type(screen.getByLabelText("邮箱"), "couple@example.com");
    await user.type(screen.getByLabelText("密码"), "incorrect");
    await user.click(screen.getByRole("button", { name: "登录账号" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("邮箱或密码错误");
    expect(localStorage.getItem("aiwardrobe_token")).toBeNull();
    await user.clear(screen.getByLabelText("密码"));
    await user.type(screen.getByLabelText("密码"), "corrected-password");
    await user.click(screen.getByRole("button", { name: "登录账号" }));
    await screen.findByRole("heading", { name: "买衣服前，先让 AI 帮你看看值不值得买" });
    expect(localStorage.getItem("aiwardrobe_token")).toBe("new-session");
    expect(JSON.parse(String(vi.mocked(fetch).mock.calls.find(([, init]) => init?.body?.toString().includes("corrected-password"))?.[1]?.body))).toEqual({ email: "couple@example.com", password: "corrected-password" });
  });

  test.each([false, true])("tag edits use the trusted save or pending confirmation boundary (pending=%s)", async (pending) => {
    const user = userEvent.setup();
    const original = pending ? pendingGarment : garment;
    const saved = { ...original, review_status: "confirmed", user_confirmed_at: "2026-10-02T00:00:00Z", tags: ["婚礼", "简约"] };
    localStorage.setItem("aiwardrobe_token", "token");
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      const path = String(input);
      if (path.startsWith("/v2/garments?")) return jsonResponse({ items: [original], has_more: false });
      if (path === `/v2/garments/${original.id}/confirm`) return jsonResponse({});
      if (path === `/v2/garments/${original.id}` || (path === `/garments/${original.id}` && init?.method === "PATCH")) return jsonResponse(saved);
      return jsonResponse({ detail: "unexpected request" }, 500);
    });
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "衣橱" }));
    await user.click(await screen.findByText(/white/));
    await user.clear(screen.getByLabelText("标签"));
    await user.type(screen.getByLabelText("标签"), " 婚礼, 简约 ");
    await user.click(screen.getByRole("button", { name: pending ? "确认并加入衣橱" : "保存标签" }));
    await screen.findByRole("heading", { name: "我的衣橱" });
    expect(screen.getByText("婚礼", { exact: true })).toBeInTheDocument();
    expect(screen.getByText("简约", { exact: true })).toBeInTheDocument();
    const mutation = vi.mocked(fetch).mock.calls.find(([, init]) => init?.method === (pending ? "POST" : "PATCH"));
    expect(mutation?.[0]).toBe(pending ? `/v2/garments/${original.id}/confirm` : `/garments/${original.id}`);
    expect(JSON.parse(String(mutation?.[1]?.body))).toEqual({ tags: ["婚礼", "简约"] });
    expect(screen.queryByText("待确认单品")).not.toBeInTheDocument();
  });

  test("failed tag edits retain the draft and never report a saved garment", async () => {
    const user = userEvent.setup();
    localStorage.setItem("aiwardrobe_token", "token");
    vi.mocked(fetch).mockImplementation(async (_input, init) => init?.method === "PATCH"
      ? jsonResponse({ detail: "保存服务暂不可用" }, 503)
      : jsonResponse({ items: [garment], has_more: false }));
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "衣橱" }));
    await user.click(await screen.findByText(/white/));
    await user.clear(screen.getByLabelText("标签"));
    await user.type(screen.getByLabelText("标签"), "婚礼");
    await user.click(screen.getByRole("button", { name: "保存标签" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("保存服务暂不可用");
    expect(screen.getByLabelText("标签")).toHaveValue("婚礼");
    expect(screen.getByRole("button", { name: "保存标签" })).not.toBeDisabled();
  });

  test("cancelled deletion sends no request and a rejected deletion keeps the item visible", async () => {
    const user = userEvent.setup();
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValue(true);
    localStorage.setItem("aiwardrobe_token", "token");
    vi.mocked(fetch).mockImplementation(async (_input, init) => init?.method === "DELETE"
      ? jsonResponse({ detail: "仍有关联记录" }, 409)
      : jsonResponse({ items: [garment], has_more: false }));
    try {
      render(<App />);
      await user.click(await screen.findByRole("button", { name: "衣橱" }));
      await user.click(await screen.findByText(/white/));
      await user.click(screen.getByRole("button", { name: "删除衣服" }));
      expect(vi.mocked(fetch).mock.calls.some(([, init]) => init?.method === "DELETE")).toBe(false);
      await user.click(screen.getByRole("button", { name: "删除衣服" }));
      expect(await screen.findByRole("alert")).toHaveTextContent("仍有关联记录");
      expect(screen.getByRole("heading", { name: "编辑单品标签" })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "删除衣服" })).not.toBeDisabled();
    } finally { confirm.mockRestore(); }
  });

  test("an empty wardrobe filter can be cleared and selection can be cancelled without deletion", async () => {
    const user = userEvent.setup();
    localStorage.setItem("aiwardrobe_token", "token");
    vi.mocked(fetch).mockImplementation(async (input) => jsonResponse({ items: String(input).includes("q=missing") ? [] : [garment, secondGarment], has_more: false }));
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "衣橱" }));
    await user.click(screen.getByRole("button", { name: "上衣" }));
    await user.type(screen.getByLabelText("搜索衣服"), "missing");
    await waitFor(() => expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).includes("q=missing"))).toBe(true));
    await user.click(screen.getByRole("button", { name: "清除筛选" }));
    expect(screen.getByLabelText("搜索衣服")).toHaveValue("");
    expect(screen.getByRole("button", { name: "全部" })).toHaveAttribute("aria-pressed", "true");
    await screen.findByText(/white/);
    await user.click(screen.getByRole("button", { name: "批量管理" }));
    const selections = screen.getAllByRole("checkbox");
    await user.click(selections[0]);
    expect(screen.getByRole("status")).toHaveTextContent("已选 1 件");
    await user.click(selections[0]);
    expect(screen.getByRole("button", { name: "删除所选" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "取消管理" }));
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
    expect(vi.mocked(fetch).mock.calls.some(([, init]) => init?.method === "DELETE")).toBe(false);
  });

  test("preference settings preserve money bounds and remove explicitly unchecked categories", async () => {
    const user = userEvent.setup();
    const preference = { primary_goal: "常穿", scenes: ["通勤"], styles: ["简约"], avoid_types: [], budget_range: "", budget_min: "100", budget_max: "300", avoid_categories: ["top"] };
    localStorage.setItem("aiwardrobe_token", "token");
    vi.mocked(fetch).mockImplementation(async (input) => String(input).startsWith("/v2/garments?")
      ? jsonResponse({ items: [garment, secondGarment, shoesGarment], has_more: false }) : jsonResponse(preference));
    render(<App />);
    await waitFor(() => expect(screen.getByLabelText("最低预算（元）")).toHaveValue(100));
    await user.clear(screen.getByLabelText("最低预算（元）"));
    await user.type(screen.getByLabelText("最低预算（元）"), "129.50");
    await user.clear(screen.getByLabelText("最高预算（元）"));
    const categories = within(screen.getByRole("group", { name: "不考虑的类别" }));
    const checked = categories.getAllByRole("checkbox").find((item) => (item as HTMLInputElement).checked)!;
    await user.click(checked);
    const alternative = categories.getAllByRole("checkbox")[1];
    await user.click(alternative);
    await user.clear(screen.getByLabelText("常用场景"));
    await user.type(screen.getByLabelText("常用场景"), " 通勤、婚礼，周末 ");
    await user.click(screen.getByRole("button", { name: "保存偏好" }));
    await waitFor(() => expect(screen.queryByLabelText("最低预算（元）")).not.toBeInTheDocument());
    const mutation = vi.mocked(fetch).mock.calls.find(([, init]) => init?.method === "PUT");
    expect(JSON.parse(String(mutation?.[1]?.body))).toMatchObject({ budget_min: "129.5", budget_max: null, currency: "CNY", scenes: ["通勤", "婚礼", "周末"], avoid_categories: ["bottom"] });
  });

  test.each([401, 503])("preference save failure (%s) expires the session or keeps a recoverable form", async (status) => {
    const user = userEvent.setup();
    localStorage.setItem("aiwardrobe_token", "token");
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      if (init?.method === "PUT") return jsonResponse({ detail: "偏好保存失败" }, status);
      return jsonResponse(String(input).startsWith("/v2/garments?") ? { items: [garment, secondGarment, shoesGarment] } : {});
    });
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "保存偏好" }));
    if (status === 401) {
      await screen.findByRole("button", { name: "注册账号" });
      expect(localStorage.getItem("aiwardrobe_token")).toBeNull();
    } else {
      expect(await screen.findByRole("alert")).toHaveTextContent("偏好保存失败");
      expect(screen.getByRole("button", { name: "保存偏好" })).not.toBeDisabled();
    }
  });

  test.each([401, 503])("report failure (%s) expires authorization or shows a recoverable error", async (status) => {
    const user = userEvent.setup();
    localStorage.setItem("aiwardrobe_token", "token");
    vi.mocked(fetch).mockImplementation(async (input) => String(input).startsWith("/v2/garments?")
      ? jsonResponse({ items: [garment] }) : jsonResponse({ detail: "报告暂不可用" }, status));
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "报告" }));
    if (status === 401) {
      await screen.findByRole("button", { name: "注册账号" });
      expect(localStorage.getItem("aiwardrobe_token")).toBeNull();
    } else expect(await screen.findByRole("alert")).toHaveTextContent("报告暂不可用");
    expect(screen.queryByText(wardrobeReport.summary)).not.toBeInTheDocument();
  });

  test("empty report distributions and empty cached recommendations are honest empty states", async () => {
    const user = userEvent.setup();
    localStorage.setItem("aiwardrobe_token", "token");
    const emptyReport = { ...wardrobeReport, summary: "暂无已确认衣物", wardrobe_gaps: [], avoid_categories: [], category_distribution: [], color_distribution: [], style_distribution: [] };
    vi.mocked(fetch).mockImplementation(async (input) => {
      const path = String(input);
      if (path.startsWith("/v2/garments?")) return jsonResponse({ items: [] });
      if (path.includes("/report")) return jsonResponse(emptyReport);
      return jsonResponse({ ...shoppingRun, cache_hit: true, items: [], wardrobe_gaps: [], avoid_categories: [] });
    });
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "报告" }));
    await screen.findByText("暂无已确认衣物");
    expect(screen.queryByText("white上衣")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "查看衣橱缺口商品" }));
    await user.click(await screen.findByRole("button", { name: "查看缺口" }));
    expect(await screen.findByText("已复用缓存")).toBeInTheDocument();
    expect(screen.queryByText("Black Work Skirt")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "你的衣橱当前缺口" })).toBeInTheDocument();
  });

  test("restored image jobs distinguish unknown outcomes, empty completion, retry and cancellation", async () => {
    const user = userEvent.setup();
    localStorage.setItem("aiwardrobe_token", "token");
    const paused = { ...taskRecord("paused-upload", "garment_split", "queued"), status: "running", stage: "awaiting_resolution", action_required: true, submission_status: "unknown" };
    const failed = { ...taskRecord("failed-upload", "garment_single", "queued"), status: "failed", error: { code: "provider_unavailable", retryable: true } };
    let retried = false;
    vi.mocked(fetch).mockImplementation(async (input) => {
      const path = String(input);
      if (path.startsWith("/v2/garments?")) return jsonResponse({ items: [] });
      if (path === "/v2/tasks?limit=50") return jsonResponse({ items: [paused, failed, taskRecord("empty-completed", "garment_single", "succeeded"), { ...taskRecord("cancelled-upload", "garment_single", "queued"), status: "cancelled" }, { ...taskRecord("irrelevant-purchase", "garment_single", "queued"), kind: "purchase_url" }] });
      if (path === "/v2/tasks/paused-upload/cancel") return jsonResponse({ ...paused, status: "cancelled", cancel_requested: true });
      if (path === "/v2/tasks/paused-upload") return jsonResponse(paused);
      if (path === "/v2/tasks/failed-upload/retry") { retried = true; return jsonResponse(taskRecord(failed.id, "garment_single", "queued")); }
      if (path === "/v2/tasks/failed-upload") return jsonResponse(taskRecord(failed.id, "garment_single", "succeeded", [pendingGarment.id]));
      if (path === `/v2/garments/${pendingGarment.id}`) return jsonResponse(pendingGarment);
      return jsonResponse({ detail: "unexpected request" }, 500);
    });
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "衣橱" }));
    await user.click(await screen.findByRole("button", { name: "上传衣服" }));
    expect(await screen.findByText("供应商结果需要人工核验。请保留任务记录，不要重新提交相同请求。")).toBeInTheDocument();
    await user.click(await screen.findByRole("button", { name: "重试此项" }));
    expect(await screen.findByText("识别结果")).toBeInTheDocument();
    expect(retried).toBe(true);
    expect(screen.getByText("待确认")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "取消任务" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "取消任务" })).not.toBeInTheDocument());
    expect(screen.getAllByText("已取消")).toHaveLength(2);
    expect(vi.mocked(fetch).mock.calls.filter(([, init]) => init?.method === "POST").map(([path]) => path)).toEqual(["/v2/tasks/failed-upload/retry", "/v2/tasks/paused-upload/cancel"]);
  });

  test.each([401, 503])("failed recovered image polling (%s) cannot invent recognized garments", async (status) => {
    const user = userEvent.setup();
    localStorage.setItem("aiwardrobe_token", "token");
    vi.mocked(fetch).mockImplementation(async (input) => {
      const path = String(input);
      if (path.startsWith("/v2/garments?")) return jsonResponse({ items: [] });
      if (path === "/v2/tasks?limit=50") return jsonResponse({ items: [taskRecord("unavailable", "garment_split", "queued")] });
      return jsonResponse({ detail: "识别进度读取失败" }, status);
    });
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "衣橱" }));
    await user.click(await screen.findByRole("button", { name: "上传衣服" }));
    if (status === 401) await screen.findByRole("button", { name: "注册账号" });
    else expect(await screen.findByRole("status")).toHaveTextContent("识别进度读取失败");
    expect(screen.queryByText("识别结果")).not.toBeInTheDocument();
  });

  test.each([401, 503])("failed image submission (%s) can be retried without reporting an analysis result", async (status) => {
    const user = userEvent.setup();
    localStorage.setItem("aiwardrobe_token", "token");
    vi.mocked(fetch).mockImplementation(async (input) => {
      const path = String(input);
      if (path.startsWith("/v2/garments?") || path === "/v2/tasks?limit=50") return jsonResponse({ items: [] });
      return jsonResponse({ detail: "上传被拒绝" }, status);
    });
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "衣橱" }));
    await user.click(await screen.findByRole("button", { name: "上传衣服" }));
    await user.upload(screen.getByLabelText("选择单件图片"), new File(["photo"], "shirt.png", { type: "image/png" }));
    if (status === 401) await screen.findByRole("button", { name: "注册账号" });
    else expect(await screen.findByRole("status")).toHaveTextContent("上传被拒绝");
    expect(screen.queryByText("识别结果")).not.toBeInTheDocument();
    expect(vi.mocked(fetch).mock.calls.some(([path]) => /^\/v2\/tasks\/[^/]+$/.test(String(path)) && String(path) !== "/v2/tasks/garment-upload")).toBe(false);
  });

  test("weather failure still permits a manual temperature and records an outfit favorite once", async () => {
    const user = userEvent.setup();
    localStorage.setItem("aiwardrobe_token", "token");
    const outfit = { id: "favorite-outfit", name: "周末婚礼", occasion: "formal", temperature: 22, source: "ai", is_favorite: false, is_fixed: false, items: [], explanation: "已确认衣物组合", weather_snapshot: null, result_revision: 3 };
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      const path = String(input);
      if (path.startsWith("/v2/garments?")) return jsonResponse({ items: [garment] });
      if (path.startsWith("/weather/")) return jsonResponse({ detail: "weather unavailable" }, 503);
      if (path === "/outfits/generate") return jsonResponse(outfit);
      if (path.endsWith("/favorite")) return jsonResponse({ ...outfit, is_favorite: true });
      if (path.endsWith("/fixed")) return jsonResponse({ ...outfit, is_favorite: true, is_fixed: JSON.parse(String(init?.body)).is_fixed });
      if (path === "/v2/feedback") return jsonResponse({ id: "favorite-feedback" });
      return jsonResponse({ detail: "unexpected request" }, 500);
    });
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "搭配" }));
    await screen.findByText("天气服务暂不可用，可继续生成");
    await user.click(screen.getByRole("button", { name: "正式场合" }));
    await user.click(screen.getByRole("button", { name: "生成搭配" }));
    await screen.findByText("周末婚礼");
    const generated = vi.mocked(fetch).mock.calls.find(([path]) => path === "/outfits/generate");
    expect(JSON.parse(String(generated?.[1]?.body))).toMatchObject({ occasion: "formal", temperature: 22, weather: null, garment_id: null });
    await user.click(screen.getByRole("button", { name: "喜欢" }));
    await screen.findByText("已记录反馈");
    expect(screen.getByRole("button", { name: "已收藏" })).toBeInTheDocument();
    const feedback = vi.mocked(fetch).mock.calls.find(([path]) => path === "/v2/feedback");
    expect(JSON.parse(String(feedback?.[1]?.body))).toMatchObject({ target_id: outfit.id, snapshot_revision: 3, value: "helpful", occasion: "formal" });
    await user.click(screen.getByRole("button", { name: "已收藏" }));
    expect(vi.mocked(fetch).mock.calls.filter(([path, init]) => String(path).endsWith("/favorite") && init?.method === "PATCH")).toHaveLength(1);
    expect(vi.mocked(fetch).mock.calls.filter(([path, init]) => path === "/v2/feedback" && init?.method === "POST")).toHaveLength(1);
    await user.click(screen.getByRole("button", { name: "保存固定搭配" }));
    await screen.findByRole("button", { name: "取消固定" });
  });

  test("shopping recommendations show a readable rate limit message", async () => {
    const user = userEvent.setup();
    localStorage.setItem("aiwardrobe_token", "token");
    mockFetchOnce({ items: [garment] });
    mockFetchOnce(wardrobeReport);
    mockFetchOnce({
      detail: {
        code: "recommendation_rate_limited",
        reset_at: "2026-06-17T10:10:00Z"
      }
    }, 429);

    render(<App />);

    await screen.findByRole("heading", { name: "买衣服前，先让 AI 帮你看看值不值得买" });
    await user.click(screen.getByRole("button", { name: "报告" }));
    await user.click(screen.getByRole("button", { name: "查看衣橱缺口商品" }));
    expect(await screen.findByRole("heading", { name: "衣橱缺口" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "查看缺口" }));

    expect(await screen.findByText(/刷新太/)).toBeInTheDocument();
  });

});
