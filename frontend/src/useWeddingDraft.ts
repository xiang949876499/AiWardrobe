import { useCallback, useEffect, useRef, useState } from "react";
import { saveWeddingStoryboard } from "./api";
import type { WeddingStoryboard, WeddingStoryDraft } from "./types";

// A stable actor identifier allows reload recovery without putting credentials in storage keys.
export function weddingActorKey(token: string) {
  try {
    const data = JSON.parse(
      atob(token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/")),
    );
    const actor = data.sub || data.participant_id;
    if (!actor) throw new Error("Actor missing");
    return String(actor);
  } catch {
    let hash = 2166136261;
    for (const char of token)
      hash = Math.imul(hash ^ char.charCodeAt(0), 16777619);
    return `session-${hash >>> 0}`;
  }
}
export const EMPTY_DRAFT_FINGERPRINT =
  "74234e98afe7498fb5daf1f36ac2d78acc339464f950703b8c019892f982b90b";
export function storyToDraft(story: WeddingStoryboard): WeddingStoryDraft {
  return {
    version: story.version,
    story_text: story.story_text,
    facts: story.facts.map((f) => ({
      source_field: f.source_field,
      text: f.text,
    })),
    shots: story.shots.map((s) => ({
      shot_index: s.shot_index,
      shot_type: s.shot_type,
      duration_ms: s.duration_ms,
      person_keys: s.person_keys,
      fact_indexes: s.fact_ids
        .map((id) => story.facts.findIndex((f) => f.id === id))
        .filter((i) => i >= 0),
      description: s.description,
      subtitle: s.subtitle,
      creative_scene: s.creative_scene,
    })),
  };
}
function valid(draft: WeddingStoryDraft) {
  return (
    draft.facts.length > 0 &&
    draft.facts.every((f) => f.text.trim() && f.source_field.trim()) &&
    draft.shots.length > 0
  );
}

export function useWeddingDraft(
  token: string | null,
  projectId: string | null,
  owner: boolean,
  initial: WeddingStoryDraft,
  onSaved?: (story: WeddingStoryboard) => void,
) {
  const [draft, displayDraft] = useState(initial);
  const [status, setStatus] = useState("等待项目");
  const [dirty, displayDirty] = useState(false);
  const current = useRef(initial);
  const isDirty = useRef(false);
  const fingerprint = useRef(EMPTY_DRAFT_FINGERPRINT);
  const remote = useRef<WeddingStoryboard | null>(null);
  const initialized = useRef(false);
  const blocked = useRef(false);
  const alive = useRef(true);
  const pending = useRef<Promise<WeddingStoryboard | null> | null>(null);
  const revision = useRef(0);
  const getRevision = useCallback(() => revision.current, []);
  const key =
    token && projectId
      ? `wedding-draft:${owner ? "owner" : "participant"}:${weddingActorKey(token)}:${projectId}:${initialized.current ? current.current.version : initial.version}`
      : null;
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  const persist = useCallback(() => {
    if (!owner || !key) return;
    try {
      localStorage.setItem(
        key,
        JSON.stringify({
          draft: current.current,
          fingerprint: fingerprint.current,
        }),
      );
    } catch {
      if (alive.current) setStatus("本地恢复存储失败，请复制草稿备份");
    }
  }, [key, owner]);
  const receive = useCallback(
    (story: WeddingStoryboard | null) => {
      remote.current = story;
      if (
        initialized.current &&
        isDirty.current &&
        current.current.version !== initial.version
      ) {
        blocked.current = true;
        setStatus("草稿冲突：内容版本已改变。保留本地编辑，请比较后重新载入");
        return;
      }
      if (!initialized.current) {
        initialized.current = true;
        let saved: { draft: WeddingStoryDraft; fingerprint: string } | null =
          null;
        try {
          const raw = owner && key ? localStorage.getItem(key) : null;
          saved = raw ? JSON.parse(raw) : null;
        } catch {
          /* Invalid recovery is ignored. */
        }
        if (
          saved?.draft?.version === initial.version &&
          Array.isArray(saved.draft.facts) &&
          Array.isArray(saved.draft.shots) &&
          typeof saved.draft.story_text === "string" &&
          typeof saved.fingerprint === "string" &&
          (!story || story.status === "draft")
        ) {
          current.current = saved.draft;
          displayDraft(saved.draft);
          fingerprint.current = saved.fingerprint;
          isDirty.current = true;
          displayDirty(true);
          blocked.current =
            saved.fingerprint !==
            (story?.draft_fingerprint || EMPTY_DRAFT_FINGERPRINT);
          setStatus(
            blocked.current
              ? "草稿冲突：本地恢复与服务器已不同，请比较后重新载入"
              : "已恢复本地草稿，等待保存",
          );
          return;
        }
      }
      if (isDirty.current || pending.current) return;
      fingerprint.current = story?.draft_fingerprint || EMPTY_DRAFT_FINGERPRINT;
      if (story) {
        current.current = storyToDraft(story);
        displayDraft(current.current);
        setStatus("已保存");
      } else {
        current.current = initial;
        displayDraft(initial);
        setStatus("未完整草稿仅保存在本地");
      }
    },
    [initial.version, key, owner],
  );
  const setDraft = (value: WeddingStoryDraft) => {
    revision.current += 1;
    current.current = value;
    displayDraft(value);
    isDirty.current = true;
    displayDirty(true);
    persist();
    if (!blocked.current)
      setStatus(valid(value) ? "等待保存" : "事实未完整，仅本地恢复");
  };
  const flush = useCallback(async (): Promise<WeddingStoryboard | null> => {
    if (!owner || !token || !projectId || !initialized.current)
      return remote.current;
    if (blocked.current) throw new Error("DRAFT_CONFLICT");
    if (pending.current) {
      await pending.current;
      if (!alive.current) return null;
      return flush();
    }
    if (!isDirty.current) return remote.current;
    if (!valid(current.current)) {
      setStatus("事实未完整，仅本地恢复");
      throw new Error("请填写真实事实后保存；原文可以直接提交 AI 编排");
    }
    if (remote.current && remote.current.status !== "draft") {
      blocked.current = true;
      setStatus("草稿冲突：服务器已批准，请比较后重新载入");
      throw new Error("APPROVED_STORYBOARD_IMMUTABLE");
    }
    const snapshot = current.current;
    setStatus("正在保存…");
    const request = saveWeddingStoryboard(
      token,
      projectId,
      snapshot,
      !!remote.current,
      fingerprint.current,
    );
    pending.current = request;
    try {
      const story = await request;
      if (!alive.current) return null;
      remote.current = story;
      revision.current += 1;
      onSaved?.(story);
      fingerprint.current = story.draft_fingerprint || fingerprint.current;
      if (current.current === snapshot) {
        isDirty.current = false;
        displayDirty(false);
        setStatus("已保存");
        if (key) localStorage.removeItem(key);
      } else {
        persist();
        setStatus("等待保存");
      }
      pending.current = null;
      return current.current !== snapshot ? flush() : story;
    } catch (error) {
      if (alive.current) {
        const reason = error instanceof Error ? error.message : String(error);
        blocked.current = /CONFLICT|STALE|ALREADY_EXISTS|IMMUTABLE/.test(
          reason,
        );
        setStatus(
          blocked.current
            ? `草稿冲突：${reason}。保留本地编辑，请比较后重新载入`
            : `保存失败：${reason}。本地草稿已保留，请手动重试`,
        );
      }
      throw error;
    } finally {
      pending.current = null;
    }
  }, [key, owner, persist, projectId, token, onSaved]);
  useEffect(() => {
    if (
      !dirty ||
      !owner ||
      blocked.current ||
      !valid(draft) ||
      status.startsWith("保存失败")
    )
      return;
    const timer = window.setTimeout(() => {
      void flush().catch(() => {});
    }, 1000);
    return () => window.clearTimeout(timer);
  }, [draft, dirty, flush, owner, status]);
  const reload = () => {
    blocked.current = false;
    isDirty.current = false;
    displayDirty(false);
    if (key) localStorage.removeItem(key);
    receive(remote.current);
  };
  return {
    draft,
    setDraft,
    status,
    dirty,
    receive,
    flush,
    reload,
    getFingerprint: () => fingerprint.current,
    getRevision,
  };
}
