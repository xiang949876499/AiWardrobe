import { ChangeEvent, forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";

import {
  cancelAnalysisTask,
  clearIdempotencyKey,
  createIdempotencyKey,
  createPurchaseImageTask,
  createPurchaseUrlTask,
  fetchAnalysisTasks,
  fetchV2Candidate,
  fingerprintFile,
  fingerprintText,
  retryAnalysisTask,
  reconcileAnalysisTask,
  stableIdempotencyKey,
  waitForAnalysisTask
} from "./api";
import type { AnalysisTask, CandidateV2 } from "./types";
import { compressImageFile } from "./imageCompression";

export type PurchaseTaskControls = { start: (url: string, image?: File) => Promise<void> };

export const ResumablePurchasePanel = forwardRef<PurchaseTaskControls, {
  token: string;
  url: string;
  onAuthExpired: () => void;
  onAnalyzed: () => void;
  embedded?: boolean;
  onCandidate?: (candidate: CandidateV2 | null) => void;
  onError?: (message: string) => void;
}>(({ token, url, onAuthExpired, onAnalyzed, embedded = false, onCandidate, onError }, ref) => {
  const [file, setFile] = useState<File | null>(null);
  const [tasks, setTasks] = useState<AnalysisTask[]>([]);
  const [candidate, setCandidate] = useState<CandidateV2 | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [tasksHasMore, setTasksHasMore] = useState(false);
  const [loadingMoreTasks, setLoadingMoreTasks] = useState(false);
  const taskCursor = useRef<string | null>(null);
  const taskPageControllers = useRef(new Set<AbortController>());
  const controllerSet = useRef(new Set<AbortController>());
  const keyRecords = useRef(new Map<string, { operation: string; fingerprint: string }>());
  const accountToken = useRef(token);
  accountToken.current = token;
  const sessionGeneration = useRef(0);
  const activeTaskId = useRef<string | null>(null);
  const selectionGeneration = useRef(0);

  function sessionIsCurrent(generation: number, signal?: AbortSignal) {
    return accountToken.current === token && sessionGeneration.current === generation && !signal?.aborted;
  }

  function selectTask(id: string | null) {
    activeTaskId.current = id;
    selectionGeneration.current += 1;
    setCandidate(null);
    onCandidate?.(null);
    return selectionGeneration.current;
  }

  useEffect(() => {
    const generation = ++sessionGeneration.current;
    selectTask(null);
    const initialSelection = selectionGeneration.current;
    setTasks([]);
    setBusy(false);
    setError("");
    setNotice("");
    setTasksHasMore(false);
    setLoadingMoreTasks(false);
    taskCursor.current = null;
    keyRecords.current.clear();
    const controller = new AbortController();
    taskPageControllers.current.add(controller);
    void (async () => {
      try {
        const result = await fetchAnalysisTasks(token, { limit: 50, signal: controller.signal });
        const purchaseTasks = result.items.filter((task) => ["purchase_url", "purchase_image", "purchase_reanalysis"].includes(task.kind));
        if (!sessionIsCurrent(generation, controller.signal)) return;
        setTasks((current) => [...current, ...purchaseTasks.filter((task) => !current.some((item) => item.id === task.id))]);
        taskCursor.current = result.next_cursor;
        setTasksHasMore(result.has_more);
        if (selectionGeneration.current === initialSelection && purchaseTasks[0]) selectTask(purchaseTasks[0].id);
        for (const task of purchaseTasks) {
          if (!sessionIsCurrent(generation, controller.signal)) return;
          if (task.status === "succeeded" && task.id === activeTaskId.current) await loadCandidate(task, generation, controller.signal);
          else if (task.status === "queued" || task.status === "running") void poll(task.id);
        }
      } catch (err) {
        if (sessionIsCurrent(generation, controller.signal) && isAuthError(err)) onAuthExpired();
      } finally {
        taskPageControllers.current.delete(controller);
      }
    })();
    return () => {
      sessionGeneration.current += 1;
      controller.abort();
      taskPageControllers.current.forEach((active) => active.abort());
      taskPageControllers.current.clear();
      controllerSet.current.forEach((active) => active.abort());
      controllerSet.current.clear();
    };
  }, [token]);

  async function loadMoreTasks() {
    if (!taskCursor.current || loadingMoreTasks) return;
    const controller = new AbortController();
    const generation = sessionGeneration.current;
    taskPageControllers.current.add(controller);
    setLoadingMoreTasks(true);
    try {
      const result = await fetchAnalysisTasks(token, { cursor: taskCursor.current, limit: 50, signal: controller.signal });
      if (!sessionIsCurrent(generation, controller.signal)) return;
      const purchaseTasks = result.items.filter((task) => ["purchase_url", "purchase_image", "purchase_reanalysis"].includes(task.kind));
      setTasks((current) => {
        const existingIds = new Set(current.map((task) => task.id));
        return [...current, ...purchaseTasks.filter((task) => !existingIds.has(task.id))];
      });
      taskCursor.current = result.next_cursor;
      setTasksHasMore(result.has_more);
      for (const task of purchaseTasks) {
        if (task.status === "queued" || task.status === "running") void poll(task.id);
      }
    } catch (err) {
      if (sessionIsCurrent(generation, controller.signal)) {
        if (isAuthError(err)) onAuthExpired();
        else setError(errorMessage(err));
      }
    } finally {
      taskPageControllers.current.delete(controller);
      if (sessionIsCurrent(generation, controller.signal)) setLoadingMoreTasks(false);
    }
  }

  async function loadCandidate(task: AnalysisTask, generation: number, signal?: AbortSignal) {
    const selection = selectionGeneration.current;
    const isSelected = () => sessionIsCurrent(generation, signal) && task.id === activeTaskId.current && selection === selectionGeneration.current;
    if (!isSelected()) return false;
    const id = task.result_refs.find((ref) => ref.resource_type === "purchase_candidate")?.id;
    if (!id) return false;
    const value = await fetchV2Candidate(token, id, signal);
    if (isSelected()) {
      setCandidate(value);
      onCandidate?.(value);
      return true;
    }
    return false;
  }

  async function poll(taskId: string) {
    const generation = sessionGeneration.current;
    const controller = new AbortController();
    controllerSet.current.add(controller);
    try {
      const result = await waitForAnalysisTask(token, taskId, {
        signal: controller.signal,
        onUpdate: (task) => {
          if (sessionIsCurrent(generation, controller.signal)) setTasks((current) => current.map((item) => item.id === task.id ? task : item));
        }
      });
      if (!sessionIsCurrent(generation, controller.signal)) return;
      setTasks((current) => current.map((item) => item.id === result.id ? result : item));
      if (result.status === "succeeded") {
        if (await loadCandidate(result, generation, controller.signal)) onAnalyzed();
      } else if (result.error && taskId === activeTaskId.current) {
        setError(result.error.code);
        onError?.(result.error.code);
      }
      if (!sessionIsCurrent(generation, controller.signal)) return;
      if (result.status === "failed" || result.status === "cancelled" || result.status === "succeeded") {
        const key = keyRecords.current.get(result.id);
        if (key) {
          await clearIdempotencyKey(token, key.operation, key.fingerprint);
          keyRecords.current.delete(result.id);
        }
      }
    } catch (err) {
      if (sessionIsCurrent(generation, controller.signal) && !(err instanceof DOMException && err.name === "AbortError")) {
        if (isAuthError(err)) onAuthExpired();
        else if (taskId === activeTaskId.current) {
          setError(errorMessage(err));
          onError?.(errorMessage(err));
        }
      }
    } finally {
      controllerSet.current.delete(controller);
    }
  }

  async function start(urlInput: string, image?: File) {
    const generation = sessionGeneration.current;
    const selection = selectTask(null);
    const isCurrent = () => sessionIsCurrent(generation) && selection === selectionGeneration.current;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      let task: AnalysisTask;
      if (image) {
        const compressedImage = await compressImageFile(image);
        const fingerprint = `${await fingerprintFile(compressedImage)}:${urlInput.trim()}`;
        const key = await stableIdempotencyKey(token, "purchase-image", fingerprint);
        if (!isCurrent()) return;
        task = await createPurchaseImageTask(token, compressedImage, key, urlInput.trim());
        if (!isCurrent()) return;
        keyRecords.current.set(task.id, { operation: "purchase-image", fingerprint });
      } else {
        const normalizedUrl = urlInput.trim();
        const fingerprint = await fingerprintText(normalizedUrl);
        const key = await stableIdempotencyKey(token, "purchase-url", fingerprint);
        if (!isCurrent()) return;
        task = await createPurchaseUrlTask(token, normalizedUrl, key);
        if (!isCurrent()) return;
        keyRecords.current.set(task.id, { operation: "purchase-url", fingerprint });
      }
      setTasks((current) => [task, ...current.filter((item) => item.id !== task.id)]);
      activeTaskId.current = task.id;
      await poll(task.id);
    } catch (err) {
      if (!isCurrent()) return;
      if (isAuthError(err)) onAuthExpired();
      else {
        setError(errorMessage(err));
        onError?.(errorMessage(err));
      }
    } finally {
      if (isCurrent()) setBusy(false);
    }
  }

  useImperativeHandle(ref, () => ({ start }));

  async function retry(task: AnalysisTask) {
    const generation = sessionGeneration.current;
    const selection = selectTask(task.id);
    const isCurrent = () => sessionIsCurrent(generation) && selection === selectionGeneration.current;
    setBusy(true);
    setError("");
    try {
      const retried = await retryAnalysisTask(token, task.id, createIdempotencyKey());
      if (!isCurrent()) return;
      setTasks((current) => current.map((item) => item.id === task.id ? retried : item));
      await poll(task.id);
    } catch (err) {
      if (!isCurrent()) return;
      if (isAuthError(err)) onAuthExpired();
        else {
          setError(errorMessage(err));
          onError?.(errorMessage(err));
        }
    } finally {
      if (isCurrent()) setBusy(false);
    }
  }

  async function cancel(task: AnalysisTask) {
    const generation = sessionGeneration.current;
    try {
      const updated = await cancelAnalysisTask(token, task.id);
      if (!sessionIsCurrent(generation)) return;
      setTasks((current) => current.map((item) => item.id === task.id ? updated : item));
    } catch (err) {
      if (!sessionIsCurrent(generation)) return;
      if (isAuthError(err)) onAuthExpired();
      else setError(errorMessage(err));
    }
  }

  async function reconcile(task: AnalysisTask) {
    const generation = sessionGeneration.current;
    const selection = selectionGeneration.current;
    const isCurrent = () => sessionIsCurrent(generation) && selection === selectionGeneration.current;
    setBusy(true);
    setError("");
    try {
      const updated = await reconcileAnalysisTask(token, task.id);
      if (!isCurrent()) return;
      setTasks((current) => current.map((item) => item.id === task.id ? updated : item));
      setNotice("已请求只读核验，不会重新提交分析。");
      await poll(task.id);
    } catch (err) {
      if (!isCurrent()) return;
      if (isAuthError(err)) onAuthExpired();
      else setError(errorMessage(err));
    } finally { if (isCurrent()) setBusy(false); }
  }

  return (
    <section className="controlPanel resumablePurchasePanel" aria-labelledby="resumable-purchase-title">
      <h2 id="resumable-purchase-title">可恢复分析任务</h2>
      <p>任务会在后台继续运行；刷新页面后可回到这里查看阶段、失败原因和安全重试。</p>
      {!embedded && <div className="buttonRow">
        <button type="button" className="primaryButton compact" disabled={busy || !/^https?:\/\/\S+/.test(url.trim())} onClick={() => void start(url)}>
          {busy ? "任务处理中" : "提交链接分析任务"}
        </button>
        <label className="secondaryButton compact" htmlFor="task-purchase-image">选择商品图片</label>
        <input id="task-purchase-image" type="file" accept="image/*" onChange={(event: ChangeEvent<HTMLInputElement>) => setFile(event.target.files?.[0] || null)} />
        <button type="button" className="secondaryButton compact" disabled={busy || !file} onClick={() => file && void start(url, file)}>提交图片分析任务</button>
      </div>}
      {file && <p className="hint">已选择：{file.name}</p>}
      {error && <div className="alert" role="alert">{error}</div>}
      {notice && <p role="status" aria-live="polite">{notice}</p>}
      {!embedded && candidate && (
        <article className="candidateTaskResult">
          {candidate.image_url && <img src={candidate.image_url} alt={`${candidate.title || "商品"} 图片`} />}
          <div>
            <strong>{candidate.title || candidate.domain || "商品候选"}</strong>
            <p>{candidate.score === null ? "衣橱信息不足，暂不生成分数" : `建议评分 ${candidate.score}`}</p>
            <p>{candidate.reason_summary}</p>
            {candidate.stale && <p className="hint">衣橱或偏好已变化，此分析标记为过期，可在购买记录中重新分析。</p>}
          </div>
        </article>
      )}
      {tasks.length > 0 && (
        <div className="uploadList" aria-label="购买分析任务">
          {tasks.map((task) => (
            <div key={task.id} className="uploadItem" aria-busy={task.status === "queued" || task.status === "running"}>
              <strong>{taskLabel(task)}</strong>
              <span>{stageLabel(task.stage)} · {task.status}</span>
              {task.stage_durations_ms && Object.keys(task.stage_durations_ms).length > 0 && (
                <small>阶段用时：{Object.entries(task.stage_durations_ms).map(([stage, duration]) => `${stageLabel(stage as AnalysisTask["stage"])} ${duration} ms`).join(" · ")}</small>
              )}
              {task.operations?.map((operation) => (
                <small key={`${operation.operation_key}:${operation.generation}`}>
                  {operation.provider} · {operation.duration_ms} ms
                  {operation.usage?.total_tokens !== undefined ? ` · ${operation.usage.total_tokens} tokens` : ""}
                  {operation.reported_cost ? ` · ${operation.reported_cost.amount} ${operation.reported_cost.currency}` : ""}
                  {operation.error_code ? ` · ${operation.error_code}` : ""}
                </small>
              ))}
              {task.error && <small>{task.error.code}</small>}
              {task.status === "cancelled" && <small>已停止本地处理{["submitted", "submitting", "unknown"].includes(task.submission_status) ? "，远端仍可能完成" : ""}</small>}
              {task.action_required && <small>提交状态待确认，可再次查询；无法查询时请保留任务并联系运维。</small>}
              <div className="buttonRow">
                {(task.status === "queued" || task.status === "running") && <button type="button" className="secondaryButton compact" onClick={() => void cancel(task)}>取消任务</button>}
                {task.status === "failed" && task.error?.retryable && <button type="button" className="secondaryButton compact" disabled={busy} onClick={() => void retry(task)}>安全重试</button>}
                {(task.status === "running" || task.status === "cancelled") && task.stage === "awaiting_resolution" && task.action_required && <button type="button" className="secondaryButton compact" disabled={busy} onClick={() => void reconcile(task)}>再次查询供应商状态</button>}
              </div>
            </div>
          ))}
        </div>
      )}
      {tasksHasMore && <button type="button" className="secondaryButton compact loadMoreButton" disabled={loadingMoreTasks} onClick={() => void loadMoreTasks()}>{loadingMoreTasks ? "正在加载" : "加载更多分析任务"}</button>}
    </section>
  );
});

function taskLabel(task: AnalysisTask) {
  return task.kind === "purchase_image" ? "商品图片分析" : task.kind === "purchase_reanalysis" ? "候选重新分析" : "商品链接分析";
}

function stageLabel(stage: AnalysisTask["stage"]) {
  return ({ validating: "校验", downloading: "下载", preprocessing: "预处理", recognizing: "识别", scoring: "评分", persisting: "保存", awaiting_resolution: "等待核验" } as Record<string, string>)[stage] || stage;
}

function isAuthError(error: unknown) {
  return error instanceof Error && ("status" in error ? Number((error as Error & { status?: number }).status) === 401 : /invalid bearer token|not authenticated/i.test(error.message));
}

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : "操作失败，请稍后重试";
}
