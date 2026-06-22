import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import App from "./App";

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
  review_status: "confirmed",
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
    duplicate_score: 86,
    wardrobe_gap_score: 45,
    pairing_score: 72,
    decision_factors: ["somewhat similar item already owned"]
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

function mockFetchOnce(body: unknown, status = 200) {
  vi.mocked(fetch).mockResolvedValueOnce({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body
  } as Response);
}

beforeEach(() => {
  localStorage.clear();
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
});

describe("AiWardrobe app", () => {
  test("logged-in app opens on the buy-before home screen", async () => {
    localStorage.setItem("aiwardrobe_token", "token");
    mockFetchOnce({ items: [garment, secondGarment] });

    render(<App />);

    expect(await screen.findByRole("heading", { name: "买衣服前，先让 AI 帮你看看值不值得买" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "上传商品图" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "粘贴商品链接" })).toBeInTheDocument();
  });

  test("main navigation exposes the five core entries without AI try-on", async () => {
    localStorage.setItem("aiwardrobe_token", "token");
    mockFetchOnce({ items: [] });

    render(<App />);

    await screen.findByRole("heading", { name: "买衣服前，先让 AI 帮你看看值不值得买" });

    for (const label of ["首页", "衣橱", "搭配", "报告", "历史"]) {
      expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
    }
    expect(screen.queryByRole("button", { name: "AI 换装" })).not.toBeInTheDocument();
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
    mockFetchOnce({
      id: "upload-1",
      original_image_url: "/static/uploads/original.jpg",
      original_image_key: "garments/original.jpg",
      status: "ready",
      error_message: null,
      garments: [pendingGarment, secondGarment, { ...garment, id: "garment-shoes", category: "shoes", image_url: "/static/uploads/crops/shoes.jpg", thumbnail_url: "/static/uploads/crops/shoes.jpg", image_key: "garments/crops/shoes.jpg", style: "运动" }],
      created_at: "2026-05-21T00:00:00Z",
      updated_at: "2026-05-21T00:00:00Z"
    }, 201);

    render(<App />);

    await screen.findByRole("heading", { name: "买衣服前，先让 AI 帮你看看值不值得买" });
    await user.click(screen.getByRole("button", { name: "衣橱" }));
    await screen.findByText("我的衣橱");
    await user.click(screen.getByRole("button", { name: "上传衣服" }));
    await user.click(screen.getByRole("button", { name: "自动识别" }));
    const file = new File(["fake image"], "multi-look.jpg", { type: "image/jpeg" });
    await user.upload(screen.getByLabelText("选择整套或多单品照片"), file);

    expect(await screen.findByText("单品拆分完成")).toBeInTheDocument();
    expect(screen.getByText("已入库单品")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /编辑上衣/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /编辑下装/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /编辑鞋子/ })).toBeInTheDocument();
    expect(screen.queryByAltText("multi-look.jpg 原图")).not.toBeInTheDocument();
    expect(String(vi.mocked(fetch).mock.calls[1][0])).toBe("/uploads/garment-photo");
  });

  test("plain upload creates a ready tagged garment without auto split workflow", async () => {
    const user = userEvent.setup();
    localStorage.setItem("aiwardrobe_token", "token");
    mockFetchOnce({ items: [] });
    mockFetchOnce({
      ...pendingGarment,
      id: "plain-1",
      source_upload_id: null,
      image_url: "/static/uploads/plain.jpg",
      image_key: "garments/plain.jpg",
      thumbnail_url: "/static/uploads/plain.jpg",
      crop_box: null,
      ai_result: { source: "plain_upload" }
    }, 201);

    render(<App />);

    await screen.findByRole("heading", { name: "买衣服前，先让 AI 帮你看看值不值得买" });
    await user.click(screen.getByRole("button", { name: "衣橱" }));
    await screen.findByText("我的衣橱");
    await user.click(screen.getByRole("button", { name: "上传衣服" }));
    expect(screen.getByText(/普通上传不调用拆分工作流/)).toBeInTheDocument();
    const file = new File(["fake image"], "single-shirt.jpg", { type: "image/jpeg" });
    await user.upload(screen.getByLabelText("选择单件图片"), file);

    expect(await screen.findByText("已入库，可编辑标签")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /编辑上衣/ })).toBeInTheDocument();
    expect(String(vi.mocked(fetch).mock.calls[1][0])).toBe("/uploads/plain-garment");
  });

  test("clears stale session when auto upload rejects the bearer token", async () => {
    const user = userEvent.setup();
    localStorage.setItem("aiwardrobe_token", "stale-token");
    mockFetchOnce({ items: [] });
    mockFetchOnce({ detail: "Invalid bearer token" }, 401);

    render(<App />);

    await screen.findByRole("heading", { name: "买衣服前，先让 AI 帮你看看值不值得买" });
    await user.click(screen.getByRole("button", { name: "衣橱" }));
    await screen.findByText("我的衣橱");
    await user.click(screen.getByRole("button", { name: "上传衣服" }));
    await user.click(screen.getByRole("button", { name: "自动识别" }));
    const file = new File(["fake image"], "multi-look.jpg", { type: "image/jpeg" });
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
    mockFetchOnce({}, 204);

    render(<App />);

    await screen.findByRole("heading", { name: "买衣服前，先让 AI 帮你看看值不值得买" });
    await user.click(screen.getByRole("button", { name: "历史" }));
    expect(await screen.findByText("周一通勤 · 31°C")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "删除搭配" }));

    await waitFor(() => expect(screen.queryByText("周一通勤 · 31°C")).not.toBeInTheDocument());
    expect(screen.getByText("还没有搭配历史")).toBeInTheDocument();
    expect(String(vi.mocked(fetch).mock.calls[2][0])).toBe("/outfits/outfit-1");
    expect(vi.mocked(fetch).mock.calls[2][1]).toEqual(expect.objectContaining({ method: "DELETE" }));
  });

  test("purchase analysis submits a product URL and saves the candidate to wardrobe", async () => {
    const user = userEvent.setup();
    localStorage.setItem("aiwardrobe_token", "token");
    mockFetchOnce({ items: [garment] });
    mockFetchOnce(purchaseCandidate, 201);
    mockFetchOnce({
      ...garment,
      id: "saved-purchase",
      image_url: purchaseCandidate.image_url,
      image_key: purchaseCandidate.image_key,
      thumbnail_url: purchaseCandidate.thumbnail_url,
      colors: ["black"],
      tags: ["T-shirt", "casual"],
      created_at: "2026-06-16T00:00:00Z",
      updated_at: "2026-06-16T00:00:00Z"
    }, 201);

    render(<App />);

    await screen.findByRole("heading", { name: "买衣服前，先让 AI 帮你看看值不值得买" });
    await user.click(screen.getByRole("button", { name: "粘贴商品链接" }));
    await user.type(screen.getByLabelText("商品链接"), purchaseCandidate.product_url);
    await user.click(screen.getByRole("button", { name: "开始分析" }));

    expect(await screen.findByText("Black Shirt")).toBeInTheDocument();
    expect(screen.getByText("已有相似黑色上衣，但夏季搭配仍有一定价值。")).toBeInTheDocument();
    expect(screen.getByText("86%")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "加入衣橱" }));

    await waitFor(() => expect(String(vi.mocked(fetch).mock.calls[2][0])).toBe("/purchase/candidates/candidate-1/save"));
  });

  test("purchase analysis offers manual image upload when URL extraction fails", async () => {
    const user = userEvent.setup();
    localStorage.setItem("aiwardrobe_token", "token");
    mockFetchOnce({ items: [] });
    mockFetchOnce({ detail: "product_image_not_found" }, 400);
    mockFetchOnce({ ...purchaseCandidate, id: "manual-candidate", source_image_url: "manual.jpg" }, 201);

    render(<App />);

    await screen.findByRole("heading", { name: "买衣服前，先让 AI 帮你看看值不值得买" });
    await user.click(screen.getByRole("button", { name: "粘贴商品链接" }));
    await user.type(screen.getByLabelText("商品链接"), "https://shop.example.com/no-image");
    await user.click(screen.getByRole("button", { name: "开始分析" }));

    expect(await screen.findByText("未能自动找到商品图片，请上传商品图继续分析。")).toBeInTheDocument();
    const file = new File(["fake product image"], "manual.jpg", { type: "image/jpeg" });
    await user.upload(screen.getByLabelText("上传商品图片"), file);

    expect(await screen.findByText("Black Shirt")).toBeInTheDocument();
    expect(String(vi.mocked(fetch).mock.calls[2][0])).toBe("/purchase/analyze-image");
  });

  test("shopping recommendations loads products and saves analyzed items", async () => {
    const user = userEvent.setup();
    localStorage.setItem("aiwardrobe_token", "token");
    mockFetchOnce({ items: [garment] });
    mockFetchOnce(shoppingRun, 201);
    mockFetchOnce(savedShoppingGarment, 201);

    render(<App />);

    await screen.findByRole("heading", { name: "买衣服前，先让 AI 帮你看看值不值得买" });
    await user.click(screen.getByRole("button", { name: "报告" }));
    await user.click(screen.getByRole("button", { name: "获取推荐" }));

    expect(await screen.findByText("Black Work Skirt")).toBeInTheDocument();
    expect(screen.getByText("Fills a work bottom gap.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "加入衣橱" }));

    await waitFor(() => expect(String(vi.mocked(fetch).mock.calls[2][0])).toBe("/purchase/candidates/candidate-shopping-1/save"));
  });

  test("shopping recommendations show a readable rate limit message", async () => {
    const user = userEvent.setup();
    localStorage.setItem("aiwardrobe_token", "token");
    mockFetchOnce({ items: [garment] });
    mockFetchOnce({
      detail: {
        code: "recommendation_rate_limited",
        reset_at: "2026-06-17T10:10:00Z"
      }
    }, 429);

    render(<App />);

    await screen.findByRole("heading", { name: "买衣服前，先让 AI 帮你看看值不值得买" });
    await user.click(screen.getByRole("button", { name: "报告" }));
    await user.click(screen.getByRole("button", { name: "获取推荐" }));

    expect(await screen.findByText(/刷新太/)).toBeInTheDocument();
  });

});
