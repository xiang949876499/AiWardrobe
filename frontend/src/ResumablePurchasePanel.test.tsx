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
