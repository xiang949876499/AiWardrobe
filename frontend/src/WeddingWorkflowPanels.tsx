import { useState } from "react";
import {
  analyzeWeddingPhoto,
  confirmWeddingPhotoQuality,
  selectWeddingPrimaryPhoto,
  selectWeddingMusic,
  cancelWeddingCreationJob,
} from "./api";
import type {
  WeddingCreationJob,
  WeddingMusicTrack,
  WeddingPhotoReadiness,
  WeddingRender,
} from "./types";

export type WeddingAction = <T>(
  fn: () => Promise<T>,
  done?: (result: T) => void,
  success?: string,
) => Promise<T | undefined>;
export function uncertainOutcome(code: string | null | undefined) {
  return !!code && /UNKNOWN|SUBMISSION_UNCERTAIN|RECONCIL/.test(code);
}
export function CreationProgress({
  jobs,
  token,
  projectId,
  run,
  refresh,
  retry,
}: {
  jobs: WeddingCreationJob[];
  token: string;
  projectId: string;
  run: WeddingAction;
  refresh: () => Promise<void>;
  retry: (kind: "story" | "frame") => void;
}) {
  return (
    <div className="ws-creationJobs">
      {jobs.map((job) => (
        <article className="ws-job" key={job.id}>
          <div>
            <b>
              {job.kind === "story" ? "故事编排" : "关键帧制作"} ·{" "}
              {job.demo ? "DEMO ONLY / NO AI MOTION" : "LIVE"} · {job.status}
            </b>
            <span>
              {job.completed_operations}/{job.total_operations} 步
            </span>
            <progress
              aria-label={`${job.kind} 进度`}
              value={job.completed_operations}
              max={job.total_operations}
            />
            {job.error_code && <code>{job.error_code}</code>}
            {uncertainOutcome(job.error_code) && (
              <p role="alert">
                结果未知：可能已收费，需运营核对；不会自动重新提交。
              </p>
            )}
            {job.error_code?.includes("BUDGET") && (
              <p>预算等待：预留金额保持锁定，请核对余额与账单。</p>
            )}
          </div>
          {["queued", "processing"].includes(job.status) && (
            <button
              type="button"
              className="ws-secondary"
              onClick={() =>
                void run(
                  () => cancelWeddingCreationJob(token, projectId, job.id),
                  () => void refresh(),
                  "取消请求已提交",
                )
              }
            >
              取消{job.kind}任务
            </button>
          )}
          {job.status === "needs_action" &&
            [
              "PROVIDER_RESULT_INVALID",
              "FRAME_FAILED",
              "PROVIDER_UNAVAILABLE",
            ].includes(job.error_code || "") && (
              <button className="ws-secondary" onClick={() => retry(job.kind)}>
                重试 {job.kind}（LIVE 可能重新收费）
              </button>
            )}
        </article>
      ))}
    </div>
  );
}

export function PhotoReadiness({
  readiness,
  ownPerson,
  token,
  projectId,
  version,
  run,
  refresh,
}: {
  readiness: WeddingPhotoReadiness | null;
  ownPerson: "A" | "B";
  token: string;
  projectId: string;
  version: number;
  run: WeddingAction;
  refresh: () => Promise<void>;
}) {
  const [costNotice, setCostNotice] = useState("");
  return (
    <div className="ws-quality">
      <h3>照片质量与本人主参考</h3>
      {costNotice && <p role="status">{costNotice}</p>}
      {readiness?.demo_bypass && (
        <p className="ws-callout">
          DEMO 照片门槛仅用于流程演示，不代表人像质量验收。
        </p>
      )}
      {readiness?.people.map((person) => (
        <div key={person.person_key}>
          <p>
            <b>
              {person.person_key} 方：{person.usable_count}/
              {person.required_count} 张可用本人照片
            </b>{" "}
            · {person.ready ? "材料已就绪" : "请补充照片或确认质量提醒"} ·
            主参考 {person.primary_photo_asset_id?.slice(0, 8) || "未选择"}
          </p>
          {person.photos.map((photo) => (
            <article className="ws-job" key={photo.asset_id}>
              <div>
                <b>
                  {photo.asset_id.slice(0, 8)} ·{" "}
                  {photo.quality.usable ? "可用" : "尚不可用"}
                </b>
                <span>
                  {photo.quality.warnings?.join(" / ") || "无已知提醒"}
                </span>
                <small>自动检测仅提供质量信号，身份仍需本人审核。</small>
              </div>
              {person.person_key === ownPerson && (
                <div className="ws-actions">
                  <button
                    className="ws-secondary"
                    onClick={() =>
                      void run(
                        () =>
                          analyzeWeddingPhoto(token, projectId, photo.asset_id),
                        () => void refresh(),
                        "质量已重新检测",
                      )
                    }
                  >
                    检测 {photo.asset_id.slice(0, 8)} 的质量
                  </button>
                  <button
                    className="ws-secondary"
                    aria-label={`确认 ${photo.asset_id} 的质量提醒`}
                    disabled={!photo.quality.usable || !!photo.confirmed_at}
                    onClick={() =>
                      void run(
                        () =>
                          confirmWeddingPhotoQuality(
                            token,
                            projectId,
                            photo.asset_id,
                            true,
                          ),
                        () => void refresh(),
                        "质量提醒已确认",
                      )
                    }
                  >
                    本人确认提醒
                  </button>
                  {photo.confirmed_at && (
                    <button
                      className="ws-secondary"
                      onClick={() =>
                        void run(
                          () =>
                            confirmWeddingPhotoQuality(
                              token,
                              projectId,
                              photo.asset_id,
                              false,
                            ),
                          () => void refresh(),
                          "已撤回质量确认",
                        )
                      }
                    >
                      撤回质量确认
                    </button>
                  )}
                  <button
                    className="ws-secondary"
                    aria-label={`选择 ${photo.asset_id} 为本人主参考`}
                    disabled={
                      !photo.quality.usable ||
                      person.primary_photo_asset_id === photo.asset_id
                    }
                    onClick={() =>
                      void run(
                        async () => {
                          const result = await selectWeddingPrimaryPhoto(
                            token,
                            projectId,
                            ownPerson,
                            version,
                            photo.asset_id,
                          );
                          await refresh();
                          return result;
                        },
                        (result) => setCostNotice(result.cost_notice),
                        "主参考已选择；请检查重做费用说明",
                      )
                    }
                  >
                    设为本人主参考
                  </button>
                </div>
              )}
            </article>
          ))}
        </div>
      ))}
    </div>
  );
}

export function MusicChooser({
  tracks,
  token,
  projectId,
  run,
  onSelection,
}: {
  tracks: WeddingMusicTrack[];
  token: string;
  projectId: string;
  run: WeddingAction;
  onSelection: (id: string) => void;
}) {
  const [id, setId] = useState("");
  const track = tracks.find((item) => item.track_id === id);
  return (
    <div className="ws-music">
      <h3>授权音乐</h3>
      <label>
        授权音乐
        <select value={id} onChange={(event) => setId(event.target.value)}>
          <option value="">选择已授权曲目</option>
          {tracks.map((item) => (
            <option value={item.track_id} key={item.track_id}>
              {item.title}
            </option>
          ))}
        </select>
      </label>
      {track && (
        <p>
          授权 {track.license_id} · {track.license_version} ·{" "}
          {track.allowed_uses.join(" / ")} · 有效期 {track.valid_until}
        </p>
      )}
      {!tracks.length && (
        <p>
          暂无可用授权曲目。DEMO 可静音；LIVE 需要客户成片交付与婚礼播放授权。
        </p>
      )}
      <button
        className="ws-secondary"
        disabled={!id}
        onClick={() =>
          void run(
            () => selectWeddingMusic(token, projectId, id),
            () => onSelection(id),
            "授权音乐选择已保存",
          )
        }
      >
        保存音乐选择
      </button>
    </div>
  );
}

export function RenderHistory({
  renders,
  selected,
  onSelect,
  currentVersion,
}: {
  renders: WeddingRender[];
  selected: string;
  onSelect: (id: string) => void;
  currentVersion: number;
}) {
  return (
    <label>
      成片历史版本
      <select
        value={selected}
        onChange={(event) => onSelect(event.target.value)}
      >
        {renders.map((render) => (
          <option key={render.id} value={render.id}>
            成片 {render.version} · 内容 v{render.project_version} ·{" "}
            {render.invalidated_at ? "已失效" : render.status}
            {render.project_version !== currentVersion
              ? "（旧内容，仅记录）"
              : ""}
          </option>
        ))}
      </select>
    </label>
  );
}
