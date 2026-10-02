import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PurchaseHistoryView } from "./PurchaseHistoryView";
import type { CandidateV2 } from "./types";

const candidate: CandidateV2 = {
  id: "candidate-1",
  title: "Blue shirt",
  product_url: "https://shop.example.com/item/1",
  domain: "shop.example.com",
  asset_id: "asset-1",
  image_url: "/v2/assets/asset-1/content",
  similar_items: [],
  category: "top",
  colors: ["blue"],
  style: "casual",
  material: "cotton",
  season: ["summer"],
  fit: "regular",
  tags: ["shirt"],
  analysis_source: "remote",
  actual_price: null,
  currency: null,
  recommendation: "consider",
  score: null,
  reason_summary: "衣橱信息不足，暂不生成评分。",
  analysis: {},
  coverage: { status: "insufficient", confirmed_garment_count: 0 },
  evidence: {},
  decision_status: "considering",
  analysis_status: "ready",
  ownership_state: "never_saved",
  owned_garment_id: null,
  revision: 1,
  snapshot_revision: 1,
  stale: false,
  created_at: "2026-10-01T00:00:00Z",
  updated_at: "2026-10-01T00:00:00Z"
};

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

beforeEach(() => vi.stubGlobal("fetch", vi.fn()));
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("purchase decision history", () => {
  it.each(["save", "link"])("refreshes ownership after %s with existing feedback before the next revisioned edit", async (action) => {
    const user = userEvent.setup();
    let current = { ...candidate, decision_status: "bought" as const };
    const edits: number[] = [];
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.startsWith("/v2/purchase/candidates?")) return jsonResponse({ items: [current], next_cursor: null, has_more: false });
      if (url.startsWith("/v2/feedback")) return jsonResponse({ items: [{ id: "existing-feedback", target_type: "candidate", target_id: candidate.id, value: "helpful", revoked_at: null }], next_cursor: null, has_more: false });
      if (url.startsWith("/v2/garments?")) return jsonResponse({ items: [{ id: "trusted-1", status: "ready", review_status: "confirmed", user_confirmed_at: "2026-10-01T00:00:00Z", category: "top", style: "Trusted", tags: [] }], has_more: false, next_cursor: null });
      if (url.endsWith("/save") || url.endsWith("/link-owned-garment")) {
        current = { ...current, ownership_state: "linked", owned_garment_id: "trusted-1", revision: 2 };
        return jsonResponse({ garment_id: "trusted-1", candidate_id: candidate.id, ownership_state: "linked", revision: 2 });
      }
      if (url === "/v2/purchase/candidates/candidate-1") {
        if (init?.method === "PATCH") {
          const body = JSON.parse(String(init.body));
          edits.push(body.expected_revision);
          if (body.expected_revision !== current.revision) return jsonResponse({ detail: { code: "revision_conflict" } }, 409);
          current = { ...current, category: body.category, revision: 3 };
        }
        return jsonResponse(current);
      }
      return jsonResponse({}, 500);
    });
    render(<PurchaseHistoryView token="token" onAuthExpired={vi.fn()} onBack={vi.fn()} />);
    await screen.findByText("Blue shirt");
    await screen.findByRole("button", { name: /撤销反馈/ });
    if (action === "save") await user.click(screen.getByRole("button", { name: "确认已购买并加入衣橱" }));
    else {
      await user.click(screen.getByRole("button", { name: "关联已有衣物" }));
      await user.selectOptions(await screen.findByRole("combobox", { name: "选择已确认的衣橱单品" }), "trusted-1");
    }
    expect(await screen.findByRole("button", { name: "已关联衣橱" })).toBeDisabled();
    await user.selectOptions(screen.getByRole("combobox", { name: "识别类别" }), "outerwear");
    await waitFor(() => expect(edits).toEqual([2]));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
  it("reuses the link idempotency key after a lost response and offers only trusted garments", async () => {
    const user = userEvent.setup();
    const keys: string[] = [];
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      const path = String(input);
      if (path.startsWith("/v2/purchase/candidates?")) return jsonResponse({ items: [{ ...candidate, ownership_state: "legacy_unlinked" }], has_more: false, next_cursor: null });
      if (path.startsWith("/v2/feedback")) return jsonResponse({ items: [], has_more: false, next_cursor: null });
      if (path.startsWith("/v2/garments?")) return jsonResponse({ items: [
        { id: "trusted-1", status: "ready", review_status: "confirmed", user_confirmed_at: "2026-10-01T00:00:00Z", category: "top", style: "可信款", tags: [] },
        { id: "pending-1", status: "ready", review_status: "pending_review", user_confirmed_at: null, category: "top", style: "待确认款", tags: [] },
        { id: "legacy-1", status: "ready", review_status: "confirmed", user_confirmed_at: null, category: "top", style: "旧资料", tags: [] }
      ], has_more: false, next_cursor: null });
      if (path.endsWith("/link-owned-garment")) {
        keys.push(new Headers(init?.headers).get("Idempotency-Key") || "");
        return keys.length === 1 ? jsonResponse({ detail: { code: "connection_lost" } }, 503) : jsonResponse({ ownership_state: "linked" });
      }
      return jsonResponse({ detail: { code: "unexpected_request" } }, 500);
    });
    render(<PurchaseHistoryView token="token" onAuthExpired={vi.fn()} onBack={vi.fn()} />);
    await user.click(await screen.findByRole("button", { name: "关联已有衣物" }));
    const select = await screen.findByRole("combobox", { name: "选择已确认的衣橱单品" });
    expect(screen.queryByRole("option", { name: /待确认款|旧资料/ })).not.toBeInTheDocument();
    await user.selectOptions(select, "trusted-1");
    expect(await screen.findByRole("alert")).toHaveTextContent("connection_lost");
    await user.selectOptions(select, "trusted-1");
    await screen.findByText("已与衣橱中的单品关联。");
    expect(keys).toHaveLength(2);
    expect(keys[0]).toMatch(/^[a-f0-9-]{36}$/i);
    expect(keys[1]).toBe(keys[0]);
  });
  it("marks an explicit purchase before linking it to the wardrobe", async () => {
    const user = userEvent.setup();
    const linkedCandidate = { ...candidate, decision_status: "bought" as const, ownership_state: "linked" as const, revision: 3 };
    let listCalls = 0;
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.startsWith("/v2/purchase/candidates?")) {
        listCalls += 1;
        return jsonResponse({ items: [listCalls === 1 ? candidate : linkedCandidate], next_cursor: null, has_more: false });
      }
      if (url.startsWith("/v2/feedback")) return jsonResponse({ items: [], next_cursor: null, has_more: false });
      if (url.endsWith("/decision")) {
        expect(JSON.parse(String(init?.body))).toEqual({ decision_status: "bought", expected_revision: 1 });
        return jsonResponse({ ...candidate, decision_status: "bought", revision: 2 });
      }
      if (url.endsWith("/save")) {
        const body = JSON.parse(String(init?.body));
        expect(body).toMatchObject({ expected_revision: 2, confirm_owned: true });
        return jsonResponse({ garment_id: "garment-1", candidate_id: candidate.id, ownership_state: "linked", revision: 3 }, 201);
      }
      return jsonResponse({ detail: { code: "unexpected_request" } }, 500);
    });

    render(<PurchaseHistoryView token="token" onAuthExpired={vi.fn()} onBack={vi.fn()} />);
    await screen.findByText("Blue shirt");
    await user.click(screen.getByRole("button", { name: "确认已购买并加入衣橱" }));

    await waitFor(() => expect(fetch).toHaveBeenCalledWith(
      "/v2/purchase/candidates/candidate-1/save",
      expect.objectContaining({ method: "POST", headers: expect.any(Headers) })
    ));
    expect(await screen.findByRole("button", { name: "已关联衣橱" })).toBeDisabled();
    expect(screen.getByText(/已加入衣橱/)).toBeInTheDocument();
  });

  it("restores saved candidate feedback and lets the user revoke it", async () => {
    const user = userEvent.setup();
    const event = {
      id: "feedback-1",
      target_type: "candidate",
      target_id: candidate.id,
      snapshot_revision: 1,
      value: "not_helpful",
      reason_code: "avoid_item",
      garment_ids: ["garment-1"],
      context: {},
      revoked_at: null,
      created_at: "2026-10-01T00:00:00Z"
    };
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.startsWith("/v2/purchase/candidates?")) {
        return jsonResponse({ items: [candidate], next_cursor: null, has_more: false });
      }
      if (url.startsWith("/v2/feedback?")) {
        return jsonResponse({ items: [event], next_cursor: null, has_more: false });
      }
      if (url === "/v2/feedback/feedback-1" && init?.method === "DELETE") return new Response(null, { status: 204 });
      return jsonResponse({ detail: { code: "unexpected_request" } }, 500);
    });

    render(<PurchaseHistoryView token="token" onAuthExpired={vi.fn()} onBack={vi.fn()} />);
    expect(await screen.findByText("已明确排除相似单品")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "撤销反馈" }));

    await waitFor(() => expect(fetch).toHaveBeenCalledWith(
      "/v2/feedback/feedback-1",
      expect.objectContaining({ method: "DELETE", headers: expect.any(Headers) })
    ));
    expect(await screen.findByText("反馈已撤销，后续建议将不再使用这条记录。")).toBeInTheDocument();
    expect(screen.queryByText("已明确排除相似单品")).not.toBeInTheDocument();
  });

  it("saves and clears a real price using the latest candidate revision", async () => {
    const user = userEvent.setup();
    let current = { ...candidate };
    const edits: Record<string, unknown>[] = [];
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.startsWith("/v2/purchase/candidates?")) return jsonResponse({ items: [current], has_more: false });
      if (url.startsWith("/v2/feedback")) return jsonResponse({ items: [], has_more: false });
      if (init?.method === "PATCH") {
        const body = JSON.parse(String(init.body)); edits.push(body);
        current = { ...current, ...body, revision: current.revision + 1 }; return jsonResponse(current);
      }
      return jsonResponse({}, 500);
    });
    render(<PurchaseHistoryView token="owner" onAuthExpired={vi.fn()} onBack={vi.fn()} />);
    const price = await screen.findByRole("spinbutton", { name: "实际价格（元）" });
    await user.type(price, "129"); await user.click(screen.getByRole("heading", { name: "购买决策记录" }));
    await waitFor(() => expect(edits).toEqual([{ expected_revision: 1, actual_price: "129", currency: "CNY" }]));
    await user.clear(price); await user.click(screen.getByRole("heading", { name: "购买决策记录" }));
    await waitFor(() => expect(edits[1]).toEqual({ expected_revision: 2, actual_price: null, currency: null }));
  });

  it("normalizes corrected tags and retains revision protection", async () => {
    const user = userEvent.setup(); const edits: unknown[] = [];
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      if (String(input).startsWith("/v2/feedback")) return jsonResponse({ items: [] });
      if (init?.method === "PATCH") { const body = JSON.parse(String(init.body)); edits.push(body); return jsonResponse({ ...candidate, tags: body.tags, revision: 2 }); }
      return jsonResponse({ items: [candidate] });
    });
    render(<PurchaseHistoryView token="owner" onAuthExpired={vi.fn()} onBack={vi.fn()} />);
    const tags = await screen.findByRole("textbox", { name: "修正标签" });
    await user.clear(tags); await user.type(tags, " 棉，通勤、清爽 "); await user.tab();
    await waitFor(() => expect(edits).toEqual([{ expected_revision: 1, tags: ["棉", "通勤", "清爽"] }]));
  });

  it.each(["skipped", "returned"] as const)("reopens a %s decision before explicitly saving ownership", async (status) => {
    const user = userEvent.setup(); let current = { ...candidate, decision_status: status as CandidateV2["decision_status"] };
    const decisions: unknown[] = [];
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.startsWith("/v2/purchase/candidates?")) return jsonResponse({ items: [current] });
      if (url.startsWith("/v2/feedback")) return jsonResponse({ items: [] });
      if (url.endsWith("/decision")) { const body = JSON.parse(String(init?.body)); decisions.push(body); current = { ...current, decision_status: body.decision_status, revision: current.revision + 1 }; return jsonResponse(current); }
      if (url.endsWith("/save")) {
        expect(JSON.parse(String(init?.body))).toEqual({ confirm_owned: true, expected_revision: 3 });
        current = { ...current, ownership_state: "linked", owned_garment_id: "garment-1", revision: 4 }; return jsonResponse({ garment_id: "garment-1" });
      }
      return jsonResponse({}, 500);
    });
    render(<PurchaseHistoryView token="owner" onAuthExpired={vi.fn()} onBack={vi.fn()} />);
    await user.click(await screen.findByRole("button", { name: "确认已购买并加入衣橱" }));
    expect(await screen.findByRole("button", { name: "已关联衣橱" })).toBeDisabled();
    expect(decisions).toEqual([{ decision_status: "considering", expected_revision: 1 }, { decision_status: "bought", expected_revision: 2 }]);
  });

  it.each(["建议有帮助", "不符合我的风格", "排除这件"])("persists %s against the exact analysis snapshot without reloading ownership", async (label) => {
    const user = userEvent.setup(); const feedback: Record<string, unknown>[] = [];
    const current = { ...candidate, similar_items: [{ garment_id: "similar-1", image_url: "/private", similarity: 81, matched_reasons: [] }] };
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.startsWith("/v2/purchase/candidates?")) return jsonResponse({ items: [current] });
      if (url === "/v2/feedback" && init?.method === "POST") { const body = JSON.parse(String(init.body)); feedback.push(body); return jsonResponse({ ...body, id: "event-1" }); }
      if (url.startsWith("/v2/feedback?")) return jsonResponse({ items: [] });
      return jsonResponse({}, 500);
    });
    render(<PurchaseHistoryView token="owner" onAuthExpired={vi.fn()} onBack={vi.fn()} />);
    await user.click(await screen.findByRole("button", { name: label }));
    expect(await screen.findByText(label === "排除这件" ? "已明确排除该单品，后续建议会遵守此选择。" : "反馈已保存，将用于后续建议。")).toBeInTheDocument();
    expect(feedback[0]).toMatchObject({ target_type: "candidate", target_id: candidate.id, snapshot_revision: 1, value: label === "建议有帮助" ? "helpful" : "not_helpful" });
    if (label === "排除这件") expect(feedback[0]).toMatchObject({ reason_code: "avoid_item", garment_ids: ["similar-1"], exclude_next_time: true });
    expect(vi.mocked(fetch).mock.calls.filter(([url]) => String(url).startsWith("/v2/purchase/candidates?")).length).toBe(1);
  });

  it("refuses feedback without a versioned snapshot", async () => {
    const user = userEvent.setup();
    vi.mocked(fetch).mockImplementation(async (input) => jsonResponse({ items: String(input).startsWith("/v2/feedback") ? [] : [{ ...candidate, snapshot_revision: 0 }] }));
    render(<PurchaseHistoryView token="owner" onAuthExpired={vi.fn()} onBack={vi.fn()} />);
    await user.click(await screen.findByRole("button", { name: "建议有帮助" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("没有可反馈的分析快照");
    expect(vi.mocked(fetch).mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
  });

  it.each([401, 409])("routes a rejected edit (%s) to auth expiry or the conflict message", async (status) => {
    const user = userEvent.setup(); const expired = vi.fn();
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      if (init?.method === "PATCH") return jsonResponse({ detail: { code: "revision_conflict" } }, status);
      return jsonResponse({ items: String(input).startsWith("/v2/feedback") ? [] : [candidate] });
    });
    render(<PurchaseHistoryView token="owner" onAuthExpired={expired} onBack={vi.fn()} />);
    await user.selectOptions(await screen.findByRole("combobox", { name: "识别类别" }), "bottom");
    if (status === 401) await waitFor(() => expect(expired).toHaveBeenCalledOnce());
    else { expect(await screen.findByRole("alert")).toHaveTextContent("revision_conflict"); expect(expired).not.toHaveBeenCalled(); }
    expect(screen.getByRole("combobox", { name: "识别类别" })).toHaveValue("top");
  });

  it("loads immutable old analysis details and collapses without another request", async () => {
    const user = userEvent.setup();
    vi.mocked(fetch).mockImplementation(async (input) => {
      const url = String(input);
      if (url === "/v2/purchase/candidates/candidate-1") return jsonResponse({ ...candidate, snapshots: [{ snapshot_revision: 1, stale: true, decision: { rule_version: "prior-rule" } }] });
      return jsonResponse({ items: url.startsWith("/v2/feedback") ? [] : [candidate] });
    });
    render(<PurchaseHistoryView token="owner" onAuthExpired={vi.fn()} onBack={vi.fn()} />);
    await user.click(await screen.findByRole("button", { name: "查看分析历史" }));
    expect(await screen.findByText(/第 1 次分析（已过期） · 规则 prior-rule/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "收起分析历史" }));
    expect(screen.queryByRole("heading", { name: "历史分析快照" })).not.toBeInTheDocument();
    expect(vi.mocked(fetch).mock.calls.filter(([url]) => url === "/v2/purchase/candidates/candidate-1").length).toBe(1);
  });

  it.each(["succeeded", "failed"])("keeps old snapshots while a reanalysis becomes %s", async (status) => {
    const user = userEvent.setup();
    const oldSnapshot = { snapshot_revision: 1, decision: { rule_version: "original-rules" } };
    const original = { ...candidate, snapshots: [oldSnapshot] };
    const result = { id: "reanalysis", status, stage: "persisting", result_refs: [], error: status === "failed" ? { code: "provider_failed" } : null };
    vi.mocked(fetch).mockImplementation(async (input) => {
      const url = String(input);
      if (url.startsWith("/v2/purchase/candidates?")) return jsonResponse({ items: [original] });
      if (url.startsWith("/v2/feedback")) return jsonResponse({ items: [] });
      if (url.endsWith("/reanalyze")) return jsonResponse(result, 202);
      if (url.startsWith("/v2/tasks/")) return jsonResponse(result);
      if (url === "/v2/purchase/candidates/candidate-1") return jsonResponse(status === "succeeded" ? { ...original, revision: 2, snapshot_revision: 2, snapshots: [oldSnapshot, { snapshot_revision: 2 }] } : original);
      return jsonResponse({}, 500);
    });
    render(<PurchaseHistoryView token="owner" onAuthExpired={vi.fn()} onBack={vi.fn()} />);
    await user.click(await screen.findByRole("button", { name: "重新分析" }));
    if (status === "succeeded") expect(await screen.findByText("分析已更新；旧快照仍保留在历史中。")).toBeInTheDocument();
    else expect(await screen.findByRole("alert")).toHaveTextContent("provider_failed");
    expect(screen.getByText("Blue shirt")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "查看分析历史" }));
    expect(await screen.findByText("第 1 次分析 · 规则 original-rules")).toBeInTheDocument();
  });

  it("appends a cursor page without restoring ownership of a removed purchase", async () => {
    const user = userEvent.setup();
    const removed = { ...candidate, id: "removed-candidate", title: "", image_url: "", score: 76, stale: true, ownership_state: "removed", coverage: { confirmed_garment_count: 3 }, snapshots: [{ snapshot_revision: 2, stale: true }] };
    vi.mocked(fetch).mockImplementation(async (input) => {
      const path = String(input);
      if (path.startsWith("/v2/feedback")) return jsonResponse({ items: [] });
      if (path.includes("cursor=")) return jsonResponse({ items: [removed], has_more: false });
      if (path === "/v2/purchase/candidates/removed-candidate") return jsonResponse(removed);
      return jsonResponse({ items: [candidate], next_cursor: "older-page", has_more: true });
    });
    render(<PurchaseHistoryView token="owner" onAuthExpired={vi.fn()} onBack={vi.fn()} />);
    await user.click(await screen.findByRole("button", { name: "加载更多购买记录" }));
    await screen.findByText("shop.example.com", { exact: true });
    expect(screen.getByText("Blue shirt")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "已从衣橱删除" })).toBeDisabled();
    expect(screen.getByText("数据覆盖：3 件已确认衣物")).toBeInTheDocument();
    await user.click(screen.getAllByRole("button", { name: "查看分析历史" })[1]);
    expect(await screen.findByText("第 2 次分析（已过期） · 规则 purchase-v1")).toBeInTheDocument();
    expect(vi.mocked(fetch).mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
  });

  it.each(["candidates", "feedback"])("a rejected %s page preserves the correct authorization failure", async (resource) => {
    const expired = vi.fn();
    vi.mocked(fetch).mockImplementation(async (input) => {
      const target = resource === "candidates" ? "/v2/purchase/candidates?" : "/v2/feedback?";
      return String(input).startsWith(target) ? jsonResponse({ detail: "session_expired" }, 401) : jsonResponse({ items: [candidate] });
    });
    render(<PurchaseHistoryView token="owner" onAuthExpired={expired} onBack={vi.fn()} />);
    await waitFor(() => expect(expired).toHaveBeenCalledOnce());
    expect(screen.queryByText("已关联衣橱")).not.toBeInTheDocument();
    expect(vi.mocked(fetch).mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
  });

  it("a cursor failure retains the first page and an explicit retry can fetch the next page", async () => {
    const user = userEvent.setup();
    let attempts = 0;
    vi.mocked(fetch).mockImplementation(async (input) => {
      const path = String(input);
      if (path.startsWith("/v2/feedback")) return jsonResponse({ items: [] });
      if (path.includes("cursor=")) {
        attempts += 1;
        return attempts === 1 ? jsonResponse({ detail: "page_unavailable" }, 503) : jsonResponse({ items: [{ ...candidate, id: "older", title: "Older shirt" }], has_more: false });
      }
      return jsonResponse({ items: [candidate], next_cursor: "older-page", has_more: true });
    });
    render(<PurchaseHistoryView token="owner" onAuthExpired={vi.fn()} onBack={vi.fn()} />);
    await user.click(await screen.findByRole("button", { name: "加载更多购买记录" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("page_unavailable");
    expect(screen.getByText("Blue shirt")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "加载更多购买记录" }));
    expect(await screen.findByText("Older shirt")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("decision filters request fresh pages and an empty trusted wardrobe cannot be linked", async () => {
    const user = userEvent.setup();
    vi.mocked(fetch).mockImplementation(async (input) => {
      const path = String(input);
      if (path.startsWith("/v2/garments?") || path.startsWith("/v2/feedback")) return jsonResponse({ items: [] });
      return jsonResponse({ items: [candidate], has_more: false });
    });
    render(<PurchaseHistoryView token="owner" onAuthExpired={vi.fn()} onBack={vi.fn()} />);
    await user.click(await screen.findByRole("button", { name: "已购买" }));
    await waitFor(() => expect(vi.mocked(fetch).mock.calls.some(([path]) => String(path).includes("decision_status=bought"))).toBe(true));
    await user.click(screen.getByRole("button", { name: "关联已有衣物" }));
    expect(await screen.findByText("没有可关联的已确认衣物。")).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: /选择已确认的衣橱单品/ }).querySelectorAll("option")).toHaveLength(1);
    await user.click(screen.getByRole("button", { name: "关联已有衣物" }));
    expect(screen.queryByRole("combobox", { name: /选择已确认的衣橱单品/ })).not.toBeInTheDocument();
    expect(vi.mocked(fetch).mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
  });

  it("loads older feedback with the server cursor", async () => {
    const user = userEvent.setup();
    const recent = {
      id: "feedback-recent",
      target_type: "candidate",
      target_id: candidate.id,
      snapshot_revision: 1,
      value: "not_helpful",
      reason_code: "avoid_item",
      garment_ids: ["garment-1"],
      context: {},
      revoked_at: null,
      created_at: "2026-10-01T00:00:00Z"
    };
    const older = { ...recent, id: "feedback-older", value: "helpful", reason_code: null, created_at: "2026-09-01T00:00:00Z" };
    vi.mocked(fetch).mockImplementation(async (input) => {
      const url = String(input);
      if (url.startsWith("/v2/purchase/candidates?")) return jsonResponse({ items: [candidate], next_cursor: null, has_more: false });
      if (url === "/v2/feedback?limit=100") return jsonResponse({ items: [recent], next_cursor: "feedback-next", has_more: true });
      if (url === "/v2/feedback?limit=100&cursor=feedback-next") return jsonResponse({ items: [older], next_cursor: null, has_more: false });
      return jsonResponse({ detail: { code: "unexpected_request", path: url } }, 500);
    });

    render(<PurchaseHistoryView token="token" onAuthExpired={vi.fn()} onBack={vi.fn()} />);
    expect(await screen.findByText("已明确排除相似单品")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "加载更多反馈记录" }));
    expect(await screen.findByText("已反馈：建议有帮助")).toBeInTheDocument();
    expect(fetch).toHaveBeenCalledWith("/v2/feedback?limit=100&cursor=feedback-next", expect.any(Object));
  });
});
