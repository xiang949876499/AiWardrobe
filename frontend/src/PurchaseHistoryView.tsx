import { useCallback, useEffect, useRef, useState } from "react";

import {
  createIdempotencyKey,
  createV2Feedback,
  decideV2Candidate,
  fetchV2Candidates,
  fetchV2Candidate,
  fetchV2Feedback,
  fetchV2Garments,
  linkV2Candidate,
  reanalyzeV2Candidate,
  revokeV2Feedback,
  saveV2Candidate,
  stableIdempotencyKey,
  updateV2Candidate,
  waitForAnalysisTask
} from "./api";
import type { CandidateV2, Category, DecisionFeedback, Garment } from "./types";

const candidateCategories: Array<{ value: Category; label: string }> = [
  { value: "top", label: "上衣" }, { value: "bottom", label: "下装" },
  { value: "outerwear", label: "外套" }, { value: "dress", label: "连衣裙" },
  { value: "shoes", label: "鞋子" }, { value: "bag", label: "包" }, { value: "accessory", label: "饰品" }
];

export function PurchaseHistoryView({ token, onAuthExpired, onBack }: {
  token: string;
  onAuthExpired: () => void;
  onBack: () => void;
}) {
  const [items, setItems] = useState<CandidateV2[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const cursorRef = useRef<string | null>(null);
  const [filter, setFilter] = useState("");
  const [busyId, setBusyId] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [expandedId, setExpandedId] = useState("");
  const [priceDrafts, setPriceDrafts] = useState<Record<string, string>>({});
  const [tagDrafts, setTagDrafts] = useState<Record<string, string>>({});
  const [linkCandidateId, setLinkCandidateId] = useState("");
  const [confirmedGarments, setConfirmedGarments] = useState<Garment[]>([]);
  const [feedbackEvents, setFeedbackEvents] = useState<Record<string, DecisionFeedback[]>>({});
  const feedbackCursor = useRef<string | null>(null);
  const [feedbackHasMore, setFeedbackHasMore] = useState(false);
  const feedbackController = useRef<AbortController | null>(null);
  const controllers = useRef(new Set<AbortController>());

  const load = useCallback(async (append = false) => {
    setError("");
    try {
      const result = await fetchV2Candidates(token, { decision_status: filter || undefined, cursor: append ? cursorRef.current || undefined : undefined, limit: 50 });
      setItems((current) => append ? [...current, ...result.items] : result.items);
      cursorRef.current = result.next_cursor;
      setHasMore(result.has_more);
    } catch (err) {
      if (isAuthError(err)) onAuthExpired();
      else setError(errorMessage(err));
    }
  }, [filter, onAuthExpired, token]);

  useEffect(() => { void load(); }, [load]);
  const loadFeedback = useCallback(async (append = false) => {
    feedbackController.current?.abort();
    const controller = new AbortController();
    feedbackController.current = controller;
    try {
      const result = await fetchV2Feedback(token, {
        limit: 100,
        cursor: append ? feedbackCursor.current || undefined : undefined,
        signal: controller.signal
      });
      if (controller.signal.aborted) return;
      feedbackCursor.current = result.next_cursor;
      setFeedbackHasMore(result.has_more);
      const pageGroups: Record<string, DecisionFeedback[]> = {};
      result.items.filter((event) => event.target_type === "candidate" && !event.revoked_at).forEach((event) => {
        pageGroups[event.target_id] = [...(pageGroups[event.target_id] || []), event];
      });
      setFeedbackEvents((current) => {
        const merged = append ? { ...current } : {};
        Object.entries(pageGroups).forEach(([candidateId, events]) => {
          const existing = merged[candidateId] || [];
          const ids = new Set(existing.map((event) => event.id));
          merged[candidateId] = [...existing, ...events.filter((event) => !ids.has(event.id))];
        });
        return merged;
      });
    } catch (err) {
      if (!controller.signal.aborted && isAuthError(err)) onAuthExpired();
      else if (!controller.signal.aborted) setError(errorMessage(err));
    }
  }, [onAuthExpired, token]);
  useEffect(() => {
    void loadFeedback();
    return () => feedbackController.current?.abort();
  }, [loadFeedback]);
  useEffect(() => () => {
    controllers.current.forEach((controller) => controller.abort());
    feedbackController.current?.abort();
  }, []);

  async function run(candidateId: string, action: () => Promise<CandidateV2 | void>) {
    setBusyId(candidateId);
    setError("");
    setMessage("");
    try {
      const updated = await action();
      if (updated) setItems((current) => current.map((item) => item.id === candidateId ? updated : item));
      else if (!feedbackEvents[candidateId]) await load();
    } catch (err) {
      if (isAuthError(err)) onAuthExpired();
      else setError(errorMessage(err));
    } finally {
      setBusyId("");
    }
  }

  function decide(candidate: CandidateV2, decision: CandidateV2["decision_status"]) {
    return run(candidate.id, () => decideV2Candidate(token, candidate.id, decision, candidate.revision));
  }

  function correctCategory(candidate: CandidateV2, category: Category) {
    return run(candidate.id, () => updateV2Candidate(token, candidate.id, {
      expected_revision: candidate.revision,
      category
    }));
  }

  function save(candidate: CandidateV2) {
    return run(candidate.id, async () => {
      if (candidate.ownership_state === "removed") throw new Error("这件候选曾从衣橱删除，不能再次自动加入。请重新分析商品。 ");
      let current = candidate;
      if (current.decision_status !== "bought") {
        if (current.decision_status === "skipped" || current.decision_status === "returned") {
          current = await decideV2Candidate(token, current.id, "considering", current.revision);
          setItems((items) => items.map((item) => item.id === current.id ? current : item));
        }
        current = await decideV2Candidate(token, current.id, "bought", current.revision);
        setItems((items) => items.map((item) => item.id === current.id ? current : item));
      }
      const result = await saveV2Candidate(token, current.id, current.revision, createIdempotencyKey());
      setMessage(`已加入衣橱（${result.garment_id.slice(0, 8)}）`);
    });
  }

  function savePrice(candidate: CandidateV2) {
    const value = (priceDrafts[candidate.id] ?? candidate.actual_price ?? "").trim();
    return run(candidate.id, () => updateV2Candidate(token, candidate.id, {
      expected_revision: candidate.revision,
      actual_price: value ? value : null,
      currency: value ? "CNY" : null
    }));
  }

  function saveTags(candidate: CandidateV2) {
    const value = (tagDrafts[candidate.id] ?? candidate.tags.join(", "));
    return run(candidate.id, () => updateV2Candidate(token, candidate.id, {
      expected_revision: candidate.revision,
      tags: value.split(/[，,、\n]/).map((item) => item.trim()).filter(Boolean)
    }));
  }

  async function showLinkOptions(candidate: CandidateV2) {
    setError("");
    if (linkCandidateId === candidate.id) {
      setLinkCandidateId("");
      return;
    }
    try {
      const result = await fetchV2Garments(token, { status: "ready", limit: 100 });
      setConfirmedGarments(result.items.filter((item) => item.status === "ready" && item.review_status === "confirmed" && Boolean(item.user_confirmed_at)));
      setLinkCandidateId(candidate.id);
    } catch (err) {
      if (isAuthError(err)) onAuthExpired();
      else setError(errorMessage(err));
    }
  }

  function linkExisting(candidate: CandidateV2, garmentId: string) {
    return run(candidate.id, async () => {
      const key = await stableIdempotencyKey(token, "candidate-link", `${candidate.id}:${garmentId}`);
      await linkV2Candidate(token, candidate.id, garmentId, candidate.revision, key);
      setMessage("已与衣橱中的单品关联。");
      setLinkCandidateId("");
    });
  }

  function feedback(candidate: CandidateV2, value: "helpful" | "not_helpful", reason?: string) {
    return run(candidate.id, async () => {
      if (!candidate.snapshot_revision) throw new Error("这条候选还没有可反馈的分析快照。");
      const event = await createV2Feedback(token, {
        target_type: "candidate",
        target_id: candidate.id,
        snapshot_revision: candidate.snapshot_revision,
        value,
        reason_code: reason
      }, createIdempotencyKey());
      setFeedbackEvents((current) => ({ ...current, [candidate.id]: [...(current[candidate.id] || []).filter((item) => item.id !== event.id), event] }));
      setMessage("反馈已保存，将用于后续建议。");
    });
  }

  function excludeSimilarItem(candidate: CandidateV2, garmentId: string) {
    return run(candidate.id, async () => {
      if (!candidate.snapshot_revision) throw new Error("这条候选还没有可反馈的分析快照。");
      const event = await createV2Feedback(token, {
        target_type: "candidate",
        target_id: candidate.id,
        snapshot_revision: candidate.snapshot_revision,
        value: "not_helpful",
        reason_code: "avoid_item",
        garment_ids: [garmentId],
        exclude_next_time: true
      }, createIdempotencyKey());
      setFeedbackEvents((current) => ({ ...current, [candidate.id]: [...(current[candidate.id] || []).filter((item) => item.id !== event.id), event] }));
      setMessage("已明确排除该单品，后续建议会遵守此选择。");
    });
  }

  function revokeFeedback(candidateId: string, eventId: string) {
    return run(candidateId, async () => {
      await revokeV2Feedback(token, eventId);
      setFeedbackEvents((current) => ({
        ...current,
        [candidateId]: (current[candidateId] || []).filter((event) => event.id !== eventId)
      }));
      setMessage("反馈已撤销，后续建议将不再使用这条记录。");
    });
  }

  function reanalyze(candidate: CandidateV2) {
    return run(candidate.id, async () => {
      const controller = new AbortController();
      controllers.current.add(controller);
      try {
        const task = await reanalyzeV2Candidate(token, candidate.id, createIdempotencyKey());
        const completed = await waitForAnalysisTask(token, task.id, {
          signal: controller.signal,
          onUpdate: (latest) => setMessage(`重新分析：${stageLabel(latest.stage)}（${latest.status}）`)
        });
        if (completed.status !== "succeeded") throw new Error(completed.error?.code || "重新分析失败");
        const detail = await fetchV2Candidate(token, candidate.id);
        setItems((current) => current.map((item) => item.id === candidate.id ? detail : item));
        setMessage("分析已更新；旧快照仍保留在历史中。");
      } finally {
        controllers.current.delete(controller);
      }
    });
  }

  async function toggleDetails(candidate: CandidateV2) {
    if (expandedId === candidate.id) {
      setExpandedId("");
      return;
    }
    setExpandedId(candidate.id);
    try {
      const detail = await fetchV2Candidate(token, candidate.id);
      setItems((current) => current.map((item) => item.id === candidate.id ? detail : item));
    } catch (err) {
      if (isAuthError(err)) onAuthExpired();
      else setError(errorMessage(err));
    }
  }

  return (
    <section className="pageSection" aria-labelledby="purchase-history-title">
      <div className="pageHeader">
        <div>
          <h1 id="purchase-history-title">购买决策记录</h1>
          <p>查看每次商品分析、修正识别结果、记录购买决定，并保留历史分析快照。</p>
        </div>
        <button type="button" className="secondaryButton compact" onClick={onBack}>返回搭配历史</button>
      </div>
      <div className="buttonRow" aria-label="购买决定筛选">
        {[ ["", "全部"], ["considering", "考虑中"], ["bought", "已购买"], ["skipped", "已跳过"], ["returned", "已退货"] ].map(([value, label]) => (
          <button key={value} type="button" className={filter === value ? "segment active" : "segment"} onClick={() => setFilter(value)}>{label}</button>
        ))}
        <button type="button" className="secondaryButton compact" onClick={() => void load()}>刷新</button>
      </div>
      {error && <div className="alert" role="alert">{error}</div>}
      {message && <p role="status" aria-live="polite">{message}</p>}
      {items.length === 0 ? (
        <div className="emptyState"><h2>还没有购买分析记录</h2><p>分析商品后，这里会保留结论和后续决定。</p></div>
      ) : (
        <div className="historyList">
          {items.map((candidate) => {
            const busy = busyId === candidate.id;
            const nextDecision = candidate.decision_status === "considering" ? "skipped"
              : candidate.decision_status === "skipped" || candidate.decision_status === "returned" ? "considering" : "returned";
            return (
              <article key={candidate.id} className="historyItem candidateHistoryItem" aria-busy={busy}>
                {candidate.image_url && <img className="purchaseHistoryImage" src={candidate.image_url} alt={`${candidate.title || "商品"} 图片`} loading="lazy" />}
                <div className="candidateHistoryBody">
                  <div className="resultHeader">
                    <strong>{candidate.title || candidate.domain || "商品候选"}</strong>
                    <span className="status">{decisionLabel(candidate.decision_status)} · {candidate.score === null ? "资料不足" : `${candidate.score} 分`}</span>
                  </div>
                  <p>{candidate.reason_summary}</p>
                  {candidate.stale && <p className="hint">分析结果已过期；可查看原快照或重新分析。</p>}
                  {candidate.similar_items.length > 0 && (
                    <div className="similarStrip" aria-label="可能重复的衣橱单品">
                      {candidate.similar_items.map((item) => (
                        <div key={item.garment_id} className="similarItem">
                          {item.image_url && <img src={item.image_url} alt="相似衣橱单品" loading="lazy" />}
                          <span>{Math.round(item.similarity)}% 相似</span>
                          <button type="button" className="ghostButton compact" disabled={busy} onClick={() => void excludeSimilarItem(candidate, item.garment_id)}>排除这件</button>
                        </div>
                      ))}
                    </div>
                  )}
                  <div className="formGrid">
                    <label>
                      识别类别
                      <select value={candidate.category} disabled={busy} onChange={(event) => void correctCategory(candidate, event.target.value as Category)}>
                        {candidateCategories.map((category) => <option key={category.value} value={category.value}>{category.label}</option>)}
                      </select>
                    </label>
                    <label>
                      实际价格（元）
                      <input type="number" min="0" step="0.01" value={priceDrafts[candidate.id] ?? candidate.actual_price ?? ""} disabled={busy} onChange={(event) => setPriceDrafts((drafts) => ({ ...drafts, [candidate.id]: event.target.value }))} onBlur={() => void savePrice(candidate)} />
                    </label>
                    <span>数据覆盖：{coverageLabel(candidate.coverage)}</span>
                  </div>
                  <label>
                    修正标签
                    <input value={tagDrafts[candidate.id] ?? candidate.tags.join(", ")} disabled={busy} onChange={(event) => setTagDrafts((drafts) => ({ ...drafts, [candidate.id]: event.target.value }))} onBlur={() => void saveTags(candidate)} />
                  </label>
                  <div className="buttonRow">
                    <button type="button" className="secondaryButton compact" disabled={busy} onClick={() => void decide(candidate, nextDecision)}>
                      {candidate.decision_status === "bought" ? "标记为退货" : candidate.decision_status === "skipped" || candidate.decision_status === "returned" ? "重新考虑" : "暂不购买"}
                    </button>
                    <button type="button" className="primaryButton compact" disabled={busy || candidate.ownership_state === "linked" || candidate.ownership_state === "removed"} onClick={() => void save(candidate)}>
                      {candidate.ownership_state === "linked" ? "已关联衣橱" : candidate.ownership_state === "removed" ? "已从衣橱删除" : "确认已购买并加入衣橱"}
                    </button>
                    <button type="button" className="secondaryButton compact" disabled={busy || candidate.ownership_state === "linked" || candidate.ownership_state === "removed"} onClick={() => void showLinkOptions(candidate)}>
                      关联已有衣物
                    </button>
                    <button type="button" className="secondaryButton compact" disabled={busy} onClick={() => void reanalyze(candidate)}>重新分析</button>
                    <button type="button" className="secondaryButton compact" disabled={busy} onClick={() => void feedback(candidate, "helpful")}>建议有帮助</button>
                    <button type="button" className="secondaryButton compact" disabled={busy} onClick={() => void feedback(candidate, "not_helpful", "style_mismatch")}>不符合我的风格</button>
                    <button type="button" className="ghostButton compact" aria-expanded={expandedId === candidate.id} onClick={() => void toggleDetails(candidate)}>
                      {expandedId === candidate.id ? "收起分析历史" : "查看分析历史"}
                    </button>
                  </div>
                  {(feedbackEvents[candidate.id] || []).map((event) => (
                    <div key={event.id} className="feedbackEventRow">
                      <span>{feedbackLabel(event)}</span>
                      <button type="button" className="ghostButton compact" disabled={busy} onClick={() => void revokeFeedback(candidate.id, event.id)}>撤销反馈</button>
                    </div>
                  ))}
                  {linkCandidateId === candidate.id && (
                    <label>
                      选择已确认的衣橱单品
                      <select value="" disabled={busy} onChange={(event) => event.target.value && void linkExisting(candidate, event.target.value)}>
                        <option value="">请选择单品</option>
                        {confirmedGarments.map((garment) => <option key={garment.id} value={garment.id}>{categoryLabel(garment.category)} · {garment.style || garment.tags.join(" / ") || garment.id.slice(0, 8)}</option>)}
                      </select>
                      {confirmedGarments.length === 0 && <span className="hint">没有可关联的已确认衣物。</span>}
                    </label>
                  )}
                  {expandedId === candidate.id && (
                    <div className="candidateSnapshots" aria-label="历史分析快照">
                      <h2>历史分析快照</h2>
                      {candidate.snapshots?.length ? candidate.snapshots.map((snapshot) => (
                        <p key={String(snapshot.snapshot_revision)}>
                          第 {String(snapshot.snapshot_revision)} 次分析{snapshot.stale ? "（已过期）" : ""} · 规则 {String((snapshot.decision as Record<string, unknown> | undefined)?.rule_version || "purchase-v1")}
                        </p>
                      )) : <p>尚无历史快照。</p>}
                    </div>
                  )}
                </div>
              </article>
            );
          })}
          {hasMore && <button type="button" className="secondaryButton" onClick={() => void load(true)}>加载更多购买记录</button>}
        </div>
      )}
      {feedbackHasMore && <button type="button" className="secondaryButton compact loadMoreButton" onClick={() => void loadFeedback(true)}>加载更多反馈记录</button>}
    </section>
  );
}

function isAuthError(error: unknown) {
  return error instanceof Error && ("status" in error ? Number((error as Error & { status?: number }).status) === 401 : /invalid bearer token|not authenticated/i.test(error.message));
}

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : "操作失败，请稍后重试";
}

function feedbackLabel(event: DecisionFeedback) {
  if (event.reason_code === "avoid_item") return "已明确排除相似单品";
  return event.value === "helpful" ? "已反馈：建议有帮助" : "已反馈：不符合我的风格";
}

function decisionLabel(value: CandidateV2["decision_status"]) {
  return { considering: "考虑中", bought: "已购买", skipped: "已跳过", returned: "已退货" }[value];
}

function categoryLabel(value: string) {
  return candidateCategories.find((category) => category.value === value)?.label || value;
}

function coverageLabel(coverage: Record<string, unknown>) {
  if (coverage.status === "insufficient") return "不足，暂不评分";
  if (typeof coverage.confirmed_garment_count === "number") return `${coverage.confirmed_garment_count} 件已确认衣物`;
  return "已记录";
}

function stageLabel(stage: string) {
  return ({ validating: "校验", downloading: "下载", preprocessing: "预处理", recognizing: "识别", scoring: "评分", persisting: "保存", awaiting_resolution: "等待核验" } as Record<string, string>)[stage] || stage;
}
