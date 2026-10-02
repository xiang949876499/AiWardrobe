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
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

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
