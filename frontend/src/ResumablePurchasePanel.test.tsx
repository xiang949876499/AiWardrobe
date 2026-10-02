import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ResumablePurchasePanel } from "./ResumablePurchasePanel";

function response(body: unknown) { return new Response(JSON.stringify(body)); }
function completedTask(id: string) {
  return { id, kind: "purchase_url", status: "succeeded", stage: "persisting", submission_status: "completed", action_required: false,
    cancel_requested: false, attempt_count: 1, result_refs: [{ resource_type: "purchase_candidate", id: `candidate-${id}` }], error: null, poll_after_seconds: null };
}
function candidate(id: string) { return { id: `candidate-${id}`, title: `${id} result`, score: null, reason_summary: "资料不足" }; }
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((complete) => { resolve = complete; });
  return { promise, resolve };
}

describe("ResumablePurchasePanel", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("restores only the newest successful task and does not replace it with older cursor results", async () => {
    const user = userEvent.setup();
    const onCandidate = vi.fn();
    vi.mocked(fetch).mockImplementation(async (input) => {
      const path = String(input);
      if (path === "/v2/tasks?limit=50") return response({ items: [completedTask("newest"), completedTask("older")], has_more: true, next_cursor: "older-page" });
      if (path.includes("cursor=older-page")) return response({ items: [completedTask("oldest")], has_more: false, next_cursor: null });
      if (path.startsWith("/v2/purchase/candidates/")) return response(candidate(path.split("candidate-")[1]));
      return response({});
    });
    render(<ResumablePurchasePanel token="token" url="" onAuthExpired={vi.fn()} onAnalyzed={vi.fn()} onCandidate={onCandidate} />);
    await screen.findByRole("button", { name: "加载更多分析任务" });
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
    expect(screen.getByText("newest result")).toBeInTheDocument();
    expect(screen.queryByText("older result")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "加载更多分析任务" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "加载更多分析任务" })).not.toBeInTheDocument());
    expect(screen.getByText("newest result")).toBeInTheDocument();
    expect(onCandidate.mock.calls.filter(([value]) => value).map(([value]) => value.id)).toEqual(["candidate-newest"]);
  });

  it("ignores an older poll completion after a newer submission becomes the active result", async () => {
    const user = userEvent.setup();
    const oldPoll = deferred<Response>();
    const oldStarted = deferred<void>();
    vi.mocked(fetch).mockImplementation(async (input) => {
      const path = String(input);
      if (path === "/v2/tasks?limit=50") return response({ items: [{ ...completedTask("older"), status: "running", result_refs: [] }], has_more: false, next_cursor: null });
      if (path === "/v2/tasks/older") { oldStarted.resolve(); return oldPoll.promise; }
      if (path === "/v2/tasks/purchase-analysis" || path === "/v2/tasks/newest") return response(completedTask("newest"));
      if (path.startsWith("/v2/purchase/candidates/")) return response(candidate(path.split("candidate-")[1]));
      return response({});
    });
    render(<ResumablePurchasePanel token="token" url="https://shop.example.com/newest" onAuthExpired={vi.fn()} onAnalyzed={vi.fn()} />);
    await oldStarted.promise;
    await user.click(screen.getByRole("button", { name: "提交链接分析任务" }));
    expect(await screen.findByText("newest result")).toBeInTheDocument();
    await act(async () => { oldPoll.resolve(response(completedTask("older"))); await new Promise((resolve) => setTimeout(resolve, 0)); });
    expect(screen.getByText("newest result")).toBeInTheDocument();
    expect(screen.queryByText("older result")).not.toBeInTheDocument();
  });

  it("keeps the active result when an older successful task page is loaded", async () => {
    const user = userEvent.setup();
    vi.mocked(fetch).mockImplementation(async (input) => {
      const path = String(input);
      if (path === "/v2/tasks?limit=50") return response({ items: [completedTask("newest")], has_more: true, next_cursor: "older-page" });
      if (path.includes("cursor=older-page")) return response({ items: [completedTask("older")], has_more: false, next_cursor: null });
      if (path.startsWith("/v2/purchase/candidates/")) return response(candidate(path.split("candidate-")[1]));
      return response({});
    });
    render(<ResumablePurchasePanel token="token" url="" onAuthExpired={vi.fn()} onAnalyzed={vi.fn()} />);
    expect(await screen.findByText("newest result")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "加载更多分析任务" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "加载更多分析任务" })).not.toBeInTheDocument());
    expect(screen.getByText("newest result")).toBeInTheDocument();
    expect(screen.queryByText("older result")).not.toBeInTheDocument();
  });

  it("ignores a restored candidate response after a new submission starts", async () => {
    const user = userEvent.setup();
    const oldResult = deferred<Response>();
    const oldStarted = deferred<void>();
    vi.mocked(fetch).mockImplementation(async (input) => {
      const path = String(input);
      if (path === "/v2/tasks?limit=50") return response({ items: [completedTask("older")], has_more: false, next_cursor: null });
      if (path.endsWith("candidate-older")) { oldStarted.resolve(); return oldResult.promise; }
      if (path === "/v2/tasks/purchase-analysis" || path === "/v2/tasks/newest") return response(completedTask("newest"));
      if (path.endsWith("candidate-newest")) return response(candidate("newest"));
      return response({});
    });
    render(<ResumablePurchasePanel token="token" url="https://shop.example.com/newest" onAuthExpired={vi.fn()} onAnalyzed={vi.fn()} />);
    await oldStarted.promise;
    await user.click(screen.getByRole("button", { name: "提交链接分析任务" }));
    expect(await screen.findByText("newest result")).toBeInTheDocument();
    await act(async () => { oldResult.resolve(response(candidate("older"))); await new Promise((resolve) => setTimeout(resolve, 0)); });
    expect(screen.getByText("newest result")).toBeInTheDocument();
    expect(screen.queryByText("older result")).not.toBeInTheDocument();
  });

  it("ignores task creation from a previous account and resets the selected candidate", async () => {
    const user = userEvent.setup();
    const oldSubmission = deferred<Response>();
    const oldStarted = deferred<void>();
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      const path = String(input);
      const account = new Headers(init?.headers).get("Authorization");
      if (path === "/v2/tasks?limit=50") return response({ items: account === "Bearer account-b" ? [completedTask("account-b")] : [], has_more: false, next_cursor: null });
      if (path === "/v2/tasks/purchase-analysis") { oldStarted.resolve(); return oldSubmission.promise; }
      if (path.startsWith("/v2/tasks/")) return response(completedTask("account-a"));
      if (path.startsWith("/v2/purchase/candidates/")) return response(candidate(path.split("candidate-")[1]));
      return response({});
    });
    const view = render(<ResumablePurchasePanel token="account-a" url="https://shop.example.com/a" onAuthExpired={vi.fn()} onAnalyzed={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "提交链接分析任务" }));
    await oldStarted.promise;
    view.rerender(<ResumablePurchasePanel token="account-b" url="https://shop.example.com/b" onAuthExpired={vi.fn()} onAnalyzed={vi.fn()} />);
    expect(await screen.findByText("account-b result")).toBeInTheDocument();
    await act(async () => { oldSubmission.resolve(response(completedTask("account-a"))); await new Promise((resolve) => setTimeout(resolve, 0)); });
    expect(screen.getByText("account-b result")).toBeInTheDocument();
    expect(screen.queryByText("account-a result")).not.toBeInTheDocument();
    expect(vi.mocked(fetch).mock.calls.some(([input]) => String(input) === "/v2/tasks/account-a")).toBe(false);
  });

  it("queries a paused provider task again without creating another analysis", async () => {
    const user = userEvent.setup();
    const task = { id: "paused-1", kind: "purchase_image", status: "running", stage: "awaiting_resolution", submission_status: "unknown", action_required: true,
      cancel_requested: false, attempt_count: 1, result_refs: [], error: null, poll_after_seconds: null };
    vi.mocked(fetch).mockImplementation(async (input) => {
      const path = String(input);
      if (path === "/v2/tasks?limit=50") return new Response(JSON.stringify({ items: [task], has_more: false, next_cursor: null }));
      if (path === "/v2/tasks/paused-1/reconcile") return new Response(JSON.stringify(task));
      if (path === "/v2/tasks/paused-1") return new Response(JSON.stringify(task));
      return new Response(JSON.stringify({ detail: { code: "unexpected_request" } }), { status: 500 });
    });
    render(<ResumablePurchasePanel token="token" url="" onAuthExpired={vi.fn()} onAnalyzed={vi.fn()} />);
    await user.click(await screen.findByRole("button", { name: "再次查询供应商状态" }));
    expect(await screen.findByText("已请求只读核验，不会重新提交分析。", { exact: true })).toBeInTheDocument();
    expect(vi.mocked(fetch).mock.calls.some(([input, init]) => String(input) === "/v2/tasks/paused-1/reconcile" && init?.method === "POST")).toBe(true);
    expect(vi.mocked(fetch).mock.calls.some(([input]) => String(input) === "/v2/tasks/purchase-analysis")).toBe(false);
  });

  it("reconciles a cancelled task without reviving local analysis or displaying remote results", async () => {
    const user = userEvent.setup();
    const onCandidate = vi.fn();
    const onAnalyzed = vi.fn();
    const cancelled = { ...completedTask("cancelled-1"), status: "cancelled", stage: "awaiting_resolution", submission_status: "unknown", action_required: true,
      cancel_requested: true, result_refs: [] };
    vi.mocked(fetch).mockImplementation(async (input) => {
      const path = String(input);
      if (path === "/v2/tasks?limit=50") return response({ items: [cancelled], has_more: false, next_cursor: null });
      if (path.endsWith("/reconcile")) return new Response(JSON.stringify(cancelled), { status: 202 });
      if (path === "/v2/tasks/cancelled-1") return response({ ...cancelled, stage: "persisting", submission_status: "completed", action_required: false });
      return response({});
    });
    render(<ResumablePurchasePanel token="token" url="" onAuthExpired={vi.fn()} onAnalyzed={onAnalyzed} onCandidate={onCandidate} />);
    await user.click(await screen.findByRole("button", { name: "再次查询供应商状态" }));
    expect(await screen.findByText("保存 · cancelled")).toBeInTheDocument();
    expect(screen.getByText("已停止本地处理")).toBeInTheDocument();
    expect(onAnalyzed).not.toHaveBeenCalled();
    expect(onCandidate.mock.calls.filter(([value]) => value)).toHaveLength(0);
    expect(vi.mocked(fetch).mock.calls.some(([input, init]) => String(input).endsWith("/reconcile") && init?.method === "POST")).toBe(true);
    expect(vi.mocked(fetch).mock.calls.some(([input]) => String(input) === "/v2/tasks/purchase-analysis" || String(input).startsWith("/v2/purchase/candidates/"))).toBe(false);
  });

  it("cancels an uncertain accepted task while preserving the remote-outcome warning", async () => {
    const user = userEvent.setup();
    const paused = { ...completedTask("paused"), status: "running", stage: "awaiting_resolution", action_required: true, submission_status: "unknown", result_refs: [],
      stage_durations_ms: { downloading: 120 }, operations: [{ operation_key: "op", generation: 1, provider: "provider", duration_ms: 100, usage: { total_tokens: 42 }, reported_cost: { amount: "0.50", currency: "CNY" }, error_code: "pending" }] };
    const analyzed = vi.fn();
    vi.mocked(fetch).mockImplementation(async (input) => {
      const path = String(input);
      if (path === "/v2/tasks?limit=50") return response({ items: [paused] });
      if (path.endsWith("/cancel")) return response({ ...paused, status: "cancelled", cancel_requested: true });
      return response(paused);
    });
    render(<ResumablePurchasePanel token="owner" url="" onAuthExpired={vi.fn()} onAnalyzed={analyzed} />);
    await user.click(await screen.findByRole("button", { name: "取消任务" }));
    expect(await screen.findByText("已停止本地处理，远端仍可能完成")).toBeInTheDocument();
    expect(screen.getByText(/42 tokens.*0.50 CNY.*pending/)).toBeInTheDocument();
    expect(analyzed).not.toHaveBeenCalled();
    expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).startsWith("/v2/purchase/candidates/"))).toBe(false);
  });

  it("an explicit safe retry reuses the task identity and keeps result provenance", async () => {
    const user = userEvent.setup(); const analyzed = vi.fn();
    const failed = { ...completedTask("failed"), status: "failed", submission_status: "not_submitted", result_refs: [], error: { code: "provider_unavailable", retryable: true } };
    vi.mocked(fetch).mockImplementation(async (input) => {
      const path = String(input);
      if (path === "/v2/tasks?limit=50") return response({ items: [failed] });
      if (path === "/v2/tasks/failed/retry") return response({ ...completedTask("failed"), status: "queued", submission_status: "not_submitted", result_refs: [] });
      if (path === "/v2/tasks/failed") return response(completedTask("failed"));
      if (path === "/v2/purchase/candidates/candidate-failed") return response({ ...candidate("failed"), title: "retry result", score: 74, stale: true, image_url: "/private-image" });
      return response({});
    });
    render(<ResumablePurchasePanel token="owner" url="" onAuthExpired={vi.fn()} onAnalyzed={analyzed} />);
    await user.click(await screen.findByRole("button", { name: "安全重试" }));
    expect(await screen.findByText("retry result")).toBeInTheDocument();
    expect(screen.getByText("建议评分 74")).toBeInTheDocument();
    expect(screen.getByText(/此分析标记为过期/)).toBeInTheDocument();
    expect(analyzed).toHaveBeenCalledOnce();
    const call = vi.mocked(fetch).mock.calls.find(([url]) => String(url).endsWith("/retry"));
    expect(new Headers(call?.[1]?.headers).get("Idempotency-Key")).toBeTruthy();
  });

  it.each([401, 503])("keeps a failed cursor load (%s) recoverable without displaying another account's candidate", async (status) => {
    const user = userEvent.setup(); const expired = vi.fn(); const shown = vi.fn();
    vi.mocked(fetch).mockImplementation(async (input) => String(input).includes("cursor=")
      ? new Response(JSON.stringify({ detail: { code: "page_unavailable" } }), { status })
      : response({ items: [], next_cursor: "older", has_more: true }));
    render(<ResumablePurchasePanel token="owner" url="" onAuthExpired={expired} onAnalyzed={vi.fn()} onCandidate={shown} />);
    await user.click(await screen.findByRole("button", { name: "加载更多分析任务" }));
    if (status === 401) await waitFor(() => expect(expired).toHaveBeenCalledOnce());
    else expect(await screen.findByRole("alert")).toHaveTextContent("page_unavailable");
    expect(shown.mock.calls.filter(([value]) => value)).toHaveLength(0);
    expect(screen.getByRole("button", { name: "加载更多分析任务" })).not.toBeDisabled();
  });

  it("unmount aborts the task-list request and ignores a late successful response", async () => {
    const loading = deferred<Response>(); const shown = vi.fn(); const expired = vi.fn();
    let signal: AbortSignal | null | undefined;
    vi.mocked(fetch).mockImplementation(async (_input, init) => { signal = init?.signal; return loading.promise; });
    const view = render(<ResumablePurchasePanel token="owner" url="" onAuthExpired={expired} onAnalyzed={vi.fn()} onCandidate={shown} />);
    view.unmount(); expect(signal?.aborted).toBe(true);
    await act(async () => loading.resolve(response({ items: [completedTask("late")] })));
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(expired).not.toHaveBeenCalled(); expect(shown.mock.calls.filter(([value]) => value)).toHaveLength(0);
  });

  it("submits an image with a stable key and the trimmed product URL", async () => {
    const user = userEvent.setup();
    vi.mocked(fetch).mockImplementation(async (input) => {
      const path = String(input);
      if (path === "/v2/tasks?limit=50") return response({ items: [] });
      if (path === "/v2/tasks/purchase-analysis") return response(completedTask("image"));
      if (path === "/v2/tasks/image") return response(completedTask("image"));
      return response(candidate("image"));
    });
    render(<ResumablePurchasePanel token="owner" url="  https://shop.example/item  " onAuthExpired={vi.fn()} onAnalyzed={vi.fn()} />);
    await user.upload(screen.getByLabelText("选择商品图片"), new File(["photo"], "item.png", { type: "image/png" }));
    await user.click(screen.getByRole("button", { name: "提交图片分析任务" }));
    expect(await screen.findByText("image result")).toBeInTheDocument();
    const call = vi.mocked(fetch).mock.calls.find(([url]) => url === "/v2/tasks/purchase-analysis");
    expect((call?.[1]?.body as FormData).get("product_url")).toBe("https://shop.example/item");
    expect(new Headers(call?.[1]?.headers).get("Idempotency-Key")).toBeTruthy();
  });

  it.each([401, 503])("a rejected task submission (%s) never enters polling", async (status) => {
    const user = userEvent.setup();
    const expired = vi.fn(); const analyzed = vi.fn();
    vi.mocked(fetch).mockImplementation(async (input) => String(input) === "/v2/tasks?limit=50"
      ? response({ items: [] })
      : new Response(JSON.stringify({ detail: "submission_denied" }), { status }));
    render(<ResumablePurchasePanel token="owner" url="https://shop.example/item" onAuthExpired={expired} onAnalyzed={analyzed} />);
    await user.click(screen.getByRole("button", { name: "提交链接分析任务" }));
    if (status === 401) await waitFor(() => expect(expired).toHaveBeenCalledOnce());
    else expect(await screen.findByRole("alert")).toHaveTextContent("submission_denied");
    expect(analyzed).not.toHaveBeenCalled();
    expect(vi.mocked(fetch).mock.calls.some(([url]) => /^\/v2\/tasks\/[^/]+$/.test(String(url)) && String(url) !== "/v2/tasks/purchase-analysis")).toBe(false);
    expect(screen.getByRole("button", { name: "提交链接分析任务" })).not.toBeDisabled();
  });

  it.each([401, 503])("poll access failure (%s) cannot display a successful candidate", async (status) => {
    const expired = vi.fn(); const analyzed = vi.fn();
    const queued = { ...completedTask("queued"), status: "queued", result_refs: [] };
    vi.mocked(fetch).mockImplementation(async (input) => String(input) === "/v2/tasks?limit=50"
      ? response({ items: [queued] })
      : new Response(JSON.stringify({ detail: "poll_denied" }), { status }));
    render(<ResumablePurchasePanel token="owner" url="" onAuthExpired={expired} onAnalyzed={analyzed} />);
    if (status === 401) await waitFor(() => expect(expired).toHaveBeenCalledOnce());
    else expect(await screen.findByRole("alert")).toHaveTextContent("poll_denied");
    expect(analyzed).not.toHaveBeenCalled();
    expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).startsWith("/v2/purchase/candidates/"))).toBe(false);
  });

  it.each([
    ["retry", "安全重试", 401], ["retry", "安全重试", 503],
    ["cancel", "取消任务", 401], ["cancel", "取消任务", 503],
    ["reconcile", "再次查询供应商状态", 401], ["reconcile", "再次查询供应商状态", 503],
  ])("%s refusal (%s, %s) preserves task state and never resubmits analysis", async (operation, label, status) => {
    const user = userEvent.setup(); const expired = vi.fn(); const analyzed = vi.fn();
    const original = { ...completedTask("original"), stage: operation === "retry" ? "persisting" : "awaiting_resolution", result_refs: [], status: operation === "retry" ? "failed" : "running", action_required: operation !== "retry",
      error: operation === "retry" ? { code: "provider_unavailable", retryable: true } : null, submission_status: operation === "retry" ? "not_submitted" : "unknown" };
    vi.mocked(fetch).mockImplementation(async (input) => {
      const path = String(input);
      if (path === "/v2/tasks?limit=50") return response({ items: [original] });
      if (path === `/v2/tasks/original/${operation}`) return new Response(JSON.stringify({ detail: "action_denied" }), { status: Number(status) });
      return response(original);
    });
    render(<ResumablePurchasePanel token="owner" url="" onAuthExpired={expired} onAnalyzed={analyzed} />);
    await user.click(await screen.findByRole("button", { name: String(label) }));
    if (status === 401) await waitFor(() => expect(expired).toHaveBeenCalledOnce());
    else expect(await screen.findByRole("alert")).toHaveTextContent("action_denied");
    expect(screen.getByText(`${operation === "retry" ? "保存" : "等待核验"} · ${original.status}`)).toBeInTheDocument();
    expect(analyzed).not.toHaveBeenCalled();
    expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url) === "/v2/tasks/purchase-analysis")).toBe(false);
  });

  it("loads older purchase analysis tasks using the server cursor", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockImplementation(async (input) => {
      const path = String(input);
      if (path === "/v2/tasks?limit=50") {
        return new Response(JSON.stringify({ items: [], next_cursor: "task-next", has_more: true }));
      }
      if (path === "/v2/tasks?cursor=task-next&limit=50") {
        return new Response(JSON.stringify({
          items: [{
            id: "purchase-task-older",
            kind: "purchase_url",
            status: "failed",
            stage: "validating",
            submission_status: "failed",
            cancel_requested: false,
            action_required: false,
            attempt_count: 1,
            error: { code: "page_fetch_failed", retryable: false },
            result_refs: [],
            poll_after_seconds: null,
            created_at: "2026-09-01T00:00:00Z",
            updated_at: "2026-09-01T00:00:00Z"
          }],
          next_cursor: null,
          has_more: false
        }));
      }
      return new Response(JSON.stringify({ detail: { code: "unexpected_request", path } }), { status: 500 });
    });

    render(<ResumablePurchasePanel token="token" url="" onAuthExpired={vi.fn()} onAnalyzed={vi.fn()} />);
    await user.click(await screen.findByRole("button", { name: "加载更多分析任务" }));

    expect(await screen.findByText("商品链接分析")).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([input]) => String(input) === "/v2/tasks?cursor=task-next&limit=50")).toBe(true);
    expect(screen.queryByRole("button", { name: "加载更多分析任务" })).not.toBeInTheDocument();
  });
});
