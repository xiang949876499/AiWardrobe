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
  status: "pending_review",
  review_status: "pending_review",
  crop_box: { x: 10, y: 20, width: 300, height: 400 }
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
  vi.unstubAllGlobals();
});

describe("AiWardrobe app", () => {
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
      status: "pending_review",
      error_message: null,
      garments: [pendingGarment],
      created_at: "2026-05-21T00:00:00Z",
      updated_at: "2026-05-21T00:00:00Z"
    }, 201);

    render(<App />);

    await screen.findByText("我的衣橱");
    await user.click(screen.getByRole("button", { name: "上传" }));
    const file = new File(["fake image"], "multi-look.jpg", { type: "image/jpeg" });
    await user.upload(screen.getByLabelText("选择服装照片"), file);

    expect(await screen.findByText("单品拆分完成")).toBeInTheDocument();
    expect(screen.getByText("待确认单品")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /确认上衣/ })).toBeInTheDocument();
  });

  test("deletes a garment from the detail page", async () => {
    const user = userEvent.setup();
    localStorage.setItem("aiwardrobe_token", "token");
    vi.stubGlobal("confirm", vi.fn(() => true));
    mockFetchOnce({ items: [garment] });
    mockFetchOnce({}, 204);

    render(<App />);

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

    await screen.findByText("我的衣橱");
    await user.click(screen.getByRole("button", { name: "搭配" }));
    expect(await screen.findByText(/当前位置 · Cloudy/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "自己搭配" }));
    await user.click(screen.getByRole("checkbox", { name: /通勤/ }));
    await user.type(screen.getByLabelText("固定搭配名称"), "周一通勤");
    await user.click(screen.getByRole("button", { name: "保存固定搭配" }));

    expect(await screen.findByText("用户保存的固定搭配")).toBeInTheDocument();
    expect(screen.getByText("固定搭配")).toBeInTheDocument();
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

    await screen.findByText("我的衣橱");
    await user.click(screen.getByRole("button", { name: "搭配" }));

    expect(await screen.findByText(/默认城市 · Cloudy/)).toBeInTheDocument();
    expect(String(vi.mocked(fetch).mock.calls[1][0])).toContain("/weather/today?lat=31.2304&lon=121.4737");
  });

  test("AI try-on entry shows unavailable feature cards", async () => {
    const user = userEvent.setup();
    localStorage.setItem("aiwardrobe_token", "token");
    mockFetchOnce({ items: [] });

    render(<App />);

    await screen.findByText("我的衣橱");
    await user.click(screen.getByRole("button", { name: "AI 换装" }));

    expect(screen.getByText("换装")).toBeInTheDocument();
    expect(screen.getByText("换发型")).toBeInTheDocument();
    expect(screen.getByText("换背景")).toBeInTheDocument();
    expect(screen.getAllByText("未开放")).toHaveLength(3);
  });
});
