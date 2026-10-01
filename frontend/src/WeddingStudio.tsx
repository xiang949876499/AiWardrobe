import { useCallback, useEffect, useRef, useState } from "react";
import {
  ArrowDownToLine,
  Check,
  CircleAlert,
  Clapperboard,
  Copy,
  Heart,
  RefreshCw,
  Send,
  Sparkles,
  Upload,
  Video,
} from "lucide-react";
import {
  acceptWeddingInvite,
  approveWeddingFrames,
  approveWeddingRender,
  approveWeddingStoryboard,
  createWeddingGenerationJob,
  createWeddingInvite,
  createWeddingProject,
  createWeddingQuote,
  createWeddingRender,
  deleteWeddingProject,
  exportWeddingRender,
  fetchWeddingAssetBlob,
  fetchWeddingCandidates,
  fetchWeddingJobs,
  fetchWeddingProject,
  fetchWeddingRenders,
  fetchWeddingStoryboard,
  getWeddingCapabilities,
  listWeddingAssets,
  listWeddingProjects,
  regenerateWeddingShot,
  revokeWeddingConsent,
  selectWeddingCandidate,
  submitWeddingRenderReview,
  submitWeddingShotReview,
  uploadWeddingPhoto,
  confirmWeddingOwnerConsent,
  acceptWeddingPhotoFallback,
  fetchWeddingCreationJobs,
  createWeddingStoryJob,
  createWeddingFrameJob,
  fetchWeddingPhotoReadiness,
  fetchWeddingMusic,
  restoreWeddingRender,
} from "./api";
import type {
  WeddingAsset,
  WeddingCandidate,
  WeddingCapabilities,
  WeddingJob,
  WeddingProject,
  WeddingProjectDetail,
  WeddingRender,
  WeddingShot,
  WeddingStoryboard,
  WeddingStoryDraft,
  WeddingStyle,
  WeddingCreationJob,
  WeddingPhotoReadiness,
  WeddingMusicTrack,
} from "./types";
import { useWeddingDraft, weddingActorKey } from "./useWeddingDraft";
import "./WeddingStudio.css";
import {
  CreationProgress,
  PhotoReadiness,
  MusicChooser,
  RenderHistory,
  uncertainOutcome,
} from "./WeddingWorkflowPanels";

const ACTIVE_PROJECT_KEY = "wedding_active_project";
const participantKey = (projectId: string) =>
  `wedding_participant:${projectId}`;
const styleLabels: Record<WeddingStyle, string> = {
  warm_documentary: "温暖纪实",
  romantic_film: "浪漫电影感",
  fresh_illustration: "清新插画",
};
function consentProviders(capabilities: WeddingCapabilities | null) {
  if (!capabilities || capabilities.demo_only) return ["local_demo"];
  const configured = [
    ...new Set(
      Object.values(capabilities.providers)
        .filter((provider) => provider.configured)
        .map((provider) => provider.provider),
    ),
  ];
  return configured.length ? configured : ["backend_storage_only"];
}

function templateDraft(
  version: number,
  style: WeddingStyle,
): WeddingStoryDraft {
  const shots: WeddingStoryDraft["shots"] = [];
  for (let i = 0; i < 18; i += 1) {
    const type = i < 8 ? "person_ai" : i < 12 ? "scene_ai" : "photo";
    shots.push({
      shot_index: i + 1,
      shot_type: type,
      duration_ms: 5000,
      person_keys: type === "scene_ai" ? [] : [i % 2 === 0 ? "A" : "B"],
      fact_indexes: [],
      description: `${styleLabels[style]} · 镜头 ${i + 1}`,
      subtitle: "",
      creative_scene: type === "scene_ai",
    });
  }
  return {
    version,
    story_text: "",
    facts: [{ source_field: "相遇", text: "" }],
    shots,
  };
}

export function PrivateMedia({
  token,
  assetId,
  video = false,
  videoLabel = "婚礼 MV 私密预览",
}: {
  token: string;
  assetId: string | null;
  video?: boolean;
  videoLabel?: string;
}) {
  const scope = `${token}:${assetId}`;
  const [loaded, setLoaded] = useState({ scope: "", url: "", error: "" });
  useEffect(() => {
    if (!assetId) return;
    let current = "";
    let alive = true;
    void fetchWeddingAssetBlob(token, assetId)
      .then((blob) => {
        current = URL.createObjectURL(blob);
        if (alive) setLoaded({ scope, url: current, error: "" });
        else URL.revokeObjectURL(current);
      })
      .catch((error) => {
        if (alive) setLoaded({ scope, url: "", error: message(error) });
      });
    return () => {
      alive = false;
      if (current) URL.revokeObjectURL(current);
    };
  }, [assetId, token]);
  const url = loaded.scope === scope ? loaded.url : "";
  if (loaded.scope === scope && loaded.error)
    return (
      <div className="ws-mediaPlaceholder" role="alert">
        素材不可用：{loaded.error}
      </div>
    );
  if (!assetId) return <div className="ws-mediaPlaceholder">尚无预览素材</div>;
  if (!url) return <div className="ws-mediaPlaceholder">正在安全读取素材…</div>;
  return video ? (
    <video
      className="ws-previewVideo"
      src={url}
      controls
      aria-label={videoLabel}
    />
  ) : (
    <img className="ws-thumb" src={url} alt="本人已授权照片" />
  );
}

export type WeddingStudioProps = {
  ownerToken: string | null;
  inviteToken?: string | null;
  initialProjectId?: string | null;
  onProjectChange?: (projectId: string) => void;
};

export default function WeddingStudio(props: WeddingStudioProps) {
  // Remount before the first paint on incoming actor/project changes, fencing every old response.
  return (
    <WeddingScope
      key={`${props.ownerToken}:${props.initialProjectId}:${props.inviteToken}`}
      {...props}
    />
  );
}

function WeddingScope(props: WeddingStudioProps) {
  const [id, setId] = useState(
    props.initialProjectId || sessionStorage.getItem(ACTIVE_PROJECT_KEY),
  );
  const [invite, setInvite] = useState(props.inviteToken);
  const [participantMode, setParticipantMode] = useState(!props.ownerToken);
  return (
    <WeddingWorkspace
      key={`${id}:${participantMode}`}
      {...props}
      inviteToken={invite}
      initialProjectId={id}
      participantMode={participantMode}
      onRoleChange={setParticipantMode}
      onProjectChange={(next) => {
        setId(next || null);
        setInvite(null);
        props.onProjectChange?.(next);
      }}
    />
  );
}

function WeddingWorkspace({
  ownerToken,
  inviteToken = null,
  initialProjectId = null,
  onProjectChange,
  participantMode = false,
  onRoleChange,
}: WeddingStudioProps & {
  participantMode?: boolean;
  onRoleChange?: (participant: boolean) => void;
}) {
  const [invite, setInvite] = useState(inviteToken);
  const [projectId, setProjectId] = useState(
    initialProjectId || sessionStorage.getItem(ACTIVE_PROJECT_KEY),
  );
  const [participantToken, setParticipantToken] = useState(() => {
    const id = initialProjectId || sessionStorage.getItem(ACTIVE_PROJECT_KEY);
    return id && participantMode
      ? sessionStorage.getItem(participantKey(id))
      : null;
  });
  const [partnerName, setPartnerName] = useState("");
  const [accepted, setAccepted] = useState(false);
  const token = participantToken || ownerToken;
  const isParticipant = !!participantToken;
  const [projects, setProjects] = useState<WeddingProject[]>([]);
  const [project, setProject] = useState<WeddingProjectDetail | null>(null);
  const [capabilities, setCapabilities] = useState<WeddingCapabilities | null>(
    null,
  );
  const [assets, setAssets] = useState<WeddingAsset[]>([]);
  const [storyboard, setStoryboard] = useState<WeddingStoryboard | null>(null);
  const [jobs, setJobs] = useState<WeddingJob[]>([]);
  const [candidates, setCandidates] = useState<WeddingCandidate[]>([]);
  const [renders, setRenders] = useState<WeddingRender[]>([]);
  const editor = useWeddingDraft(
    token,
    projectId,
    !isParticipant,
    templateDraft(project?.version || 1, project?.style || "warm_documentary"),
    setStoryboard,
  );
  const { draft, setDraft, getRevision } = editor;
  const [creationJobs, setCreationJobs] = useState<WeddingCreationJob[]>([]);
  const [readiness, setReadiness] = useState<WeddingPhotoReadiness | null>(
    null,
  );
  const [music, setMusic] = useState<WeddingMusicTrack[]>([]);
  const [musicId, setMusicId] = useState("");
  const [selectedRenderId, setSelectedRenderId] = useState("");
  const [subtitleEdits, setSubtitleEdits] = useState<Record<string, string>>(
    {},
  );
  const alive = useRef(true);
  const epoch = useRef(0);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      epoch.current += 1;
    };
  }, []);
  const loadedStoryboard = useRef(false);
  const receiveDraft = editor.receive;
  useEffect(() => {
    if (project && loadedStoryboard.current) receiveDraft(storyboard);
  }, [project, storyboard, receiveDraft]);
  const [style, setStyle] = useState<WeddingStyle>("warm_documentary");
  const [frameChoices, setFrameChoices] = useState<Record<string, string>>({});
  const [title, setTitle] = useState("我们的婚礼 MV");
  const [ownerName, setOwnerName] = useState("");
  const [inviteName, setInviteName] = useState("");
  const [inviteUrl, setInviteUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const setActiveProject = useCallback(
    (id: string) => {
      epoch.current += 1;
      setProject(null);
      setAssets([]);
      setStoryboard(null);
      setJobs([]);
      setCandidates([]);
      setRenders([]);
      setCreationJobs([]);
      setReadiness(null);
      setSelectedRenderId("");
      setFrameChoices({});
      setInviteUrl("");
      loadedStoryboard.current = false;
      setProjectId(id);
      sessionStorage.setItem(ACTIVE_PROJECT_KEY, id);
      onProjectChange?.(id);
    },
    [onProjectChange],
  );

  const refresh = useCallback(
    async (id = projectId, auth = token) => {
      if (!id || !auth) return;
      const requestEpoch = epoch.current;
      const draftRevision = getRevision();
      setError("");
      const results = await Promise.allSettled([
        fetchWeddingProject(auth, id),
        listWeddingAssets(auth, id),
        fetchWeddingStoryboard(auth, id),
        fetchWeddingJobs(auth, id),
        fetchWeddingCandidates(auth, id),
        fetchWeddingRenders(auth, id),
        isParticipant
          ? Promise.resolve([])
          : fetchWeddingCreationJobs(auth, id),
        fetchWeddingPhotoReadiness(auth, id),
      ]);
      if (!alive.current || epoch.current !== requestEpoch) return;
      const [p, a, s, j, c, r, creation, photos] = results;
      const readErrors: string[] = [];
      if (p.status === "fulfilled") setProject(p.value);
      else readErrors.push(`project: ${message(p.reason)}`);
      if (a.status === "fulfilled") setAssets(a.value);
      else readErrors.push(`assets: ${message(a.reason)}`);
      if (isParticipant || draftRevision === getRevision()) {
        if (s.status === "fulfilled") {
          setStoryboard(s.value);
          loadedStoryboard.current = true;
        } else if (
          s.reason instanceof Error &&
          ![
            "STORYBOARD_NOT_FOUND",
            "STORYBOARD_REQUIRED",
            "STORYBOARD_NOT_APPROVED",
          ].includes(s.reason.message)
        )
          setError(s.reason.message);
        else {
          loadedStoryboard.current = true;
          setStoryboard(null);
        }
      }
      if (j.status === "fulfilled") setJobs(j.value);
      else readErrors.push(`jobs: ${message(j.reason)}`);
      if (c.status === "fulfilled") setCandidates(c.value);
      else readErrors.push(`candidates: ${message(c.reason)}`);
      if (r.status === "fulfilled") setRenders(r.value);
      else readErrors.push(`renders: ${message(r.reason)}`);
      if (creation.status === "fulfilled") setCreationJobs(creation.value);
      else readErrors.push(`creation: ${message(creation.reason)}`);
      if (photos.status === "fulfilled") setReadiness(photos.value);
      else readErrors.push(`photos: ${message(photos.reason)}`);
      if (readErrors.length) setError(readErrors.join(" · "));
    },
    [projectId, token, isParticipant, getRevision],
  );

  useEffect(() => {
    let active = true;
    const scoped =
      <T,>(apply: (value: T) => void) =>
      (value: T) => {
        if (active && alive.current) apply(value);
      };
    if (!invite && ownerToken && !isParticipant) {
      void getWeddingCapabilities(ownerToken)
        .then(scoped(setCapabilities))
        .catch((e: unknown) => {
          if (active) setError(message(e));
        });
      void fetchWeddingMusic(ownerToken)
        .then(scoped((result) => setMusic(result.tracks)))
        .catch((e: unknown) => {
          if (active) setError(message(e));
        });
      if (!projectId)
        void listWeddingProjects(ownerToken)
          .then(scoped(setProjects))
          .catch((e: unknown) => {
            if (active) setError(message(e));
          });
    } else if (participantToken && projectId) {
      void getWeddingCapabilities(participantToken)
        .then(scoped(setCapabilities))
        .catch((e: unknown) => {
          if (active) setError(message(e));
        });
    }
    return () => {
      active = false;
    };
  }, [invite, ownerToken, participantToken, projectId,isParticipant]);

  useEffect(() => {
    if (!invite && projectId && token) void refresh(projectId, token);
  }, [invite, projectId, token, refresh]);

  useEffect(() => {
    const active =
      creationJobs.some(
        (job) => job.status === "queued" || job.status === "processing",
      ) ||
      jobs.some(
        (job) => job.status === "queued" || job.status === "processing",
      ) ||
      renders.some(
        (render) =>
          render.status === "queued" || render.status === "processing",
      );
    if (!active || !projectId || !token || invite) return;
    const timer = window.setInterval(() => {
      void refresh(projectId, token);
    }, 3000);
    return () => window.clearInterval(timer);
  }, [invite, jobs, creationJobs, projectId, renders, refresh, token]);

  useEffect(() => {
    if (inviteToken) setInvite(inviteToken);
  }, [inviteToken]);

  async function act<T>(
    fn: () => Promise<T>,
    done?: (result: T) => void,
    success = "已保存",
  ) {
    const requestEpoch = epoch.current;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await fn();
      if (alive.current && epoch.current === requestEpoch) {
        done?.(result);
        setNotice(success);
      }
      return result;
    } catch (e) {
      if (alive.current && epoch.current === requestEpoch) setError(message(e));
      return undefined;
    } finally {
      if (alive.current && epoch.current === requestEpoch) setBusy(false);
    }
  }

  function clearProjectState() {
    epoch.current += 1;
    sessionStorage.removeItem(ACTIVE_PROJECT_KEY);
    setProjectId(null);
    setProject(null);
    setStoryboard(null);
    setAssets([]);
    setJobs([]);
    setCandidates([]);
    setRenders([]);
    setCreationJobs([]);
    setReadiness(null);
    setFrameChoices({});
    setInviteUrl("");
    setError("");
    setNotice("");
    onProjectChange?.("");
  }

  function returnToProjectList() {
    clearProjectState();
    if (canOwner && token)
      void listWeddingProjects(token)
        .then(setProjects)
        .catch((e: unknown) => setError(message(e)));
  }

  function deleteProject() {
    if (
      !projectId ||
      !token ||
      !window.confirm(
        "删除后会移除这个项目的照片、生成素材和版本记录，且无法恢复。确定删除吗？",
      )
    )
      return;
    void act(
      () => deleteWeddingProject(token, projectId),
      () => {
        sessionStorage.removeItem(participantKey(projectId));
        clearProjectState();
        void listWeddingProjects(token)
          .then(setProjects)
          .catch((e: unknown) => setError(message(e)));
      },
      "项目及私有素材已删除",
    );
  }

  function revokeMyConsent() {
    if (
      !projectId ||
      !token ||
      !window.confirm(
        "撤回后会立即停止本人的项目素材访问并取消待处理任务。已下载的文件无法远程收回。继续吗？",
      )
    )
      return;
    void act(
      () => revokeWeddingConsent(token, projectId),
      () => {
        if (isParticipant) {
          sessionStorage.removeItem(participantKey(projectId));
          setParticipantToken(null);
          clearProjectState();
        } else {
          void refresh();
        }
      },
      "本人授权已撤回",
    );
  }

  async function acceptInvite(event: React.FormEvent) {
    event.preventDefault();
    if (!invite) return;
    const result = await act(
      () => acceptWeddingInvite(invite, partnerName.trim()),
      (response) => {
        if (!response.accepted || !response.access_token)
          throw new Error("INVITE_NOT_ACCEPTED");
        sessionStorage.setItem(
          participantKey(response.project_id),
          response.access_token,
        );
        sessionStorage.setItem(ACTIVE_PROJECT_KEY, response.project_id);
        setParticipantToken(response.access_token);
        setProjectId(response.project_id);
        setInvite(null);
        setAccepted(true);
        onRoleChange?.(true);
        const next = new URL(window.location.href);
        next.searchParams.delete("wedding_invite");
        next.searchParams.delete("wedding_project");
        window.history.replaceState(
          {},
          "",
          `${next.pathname}${next.search}${next.hash}`,
        );
        onProjectChange?.(response.project_id);
      },
      "已接受邀请，访问令牌只保存在此浏览器会话",
    );
    if (result && result.accepted) setAccepted(true);
  }

  if (invite)
    return (
      <div className="ws-shell ws-inviteShell">
        <header className="ws-brand">
          <div className="ws-mark">
            <Heart size={20} />
          </div>
          <div>
            <strong>Wedding Studio</strong>
            <span>婚礼 MV 工作台</span>
          </div>
        </header>
        <section className="ws-card ws-inviteCard">
          <p className="ws-eyebrow">专属邀请</p>
          <h1>一起制作你们的婚礼 MV</h1>
          <p>
            确认后，你将以 B
            方身份加入项目。你可以上传自己的照片并审核涉及自己的镜头。
          </p>
          <form onSubmit={(e) => void acceptInvite(e)}>
            <label htmlFor="ws-partner-name">你的称呼</label>
            <input
              id="ws-partner-name"
              required
              maxLength={20}
              value={partnerName}
              onChange={(e) => setPartnerName(e.target.value)}
              placeholder="例如：小林"
            />
            <label className="ws-check">
              <input type="checkbox" required />
              我同意将本人上传的照片用于本项目制作与预览审核。
            </label>
            <p className="ws-small">
              接受后令牌仅保存在此项目的 sessionStorage，不会替换衣橱登录状态。
            </p>
            <button
              className="ws-primary"
              disabled={busy || !partnerName.trim()}
            >
              <Check size={17} />
              接受邀请并继续
            </button>
          </form>
          {error && (
            <p className="ws-error" role="alert">
              <CircleAlert size={16} />
              {error}
            </p>
          )}
        </section>
      </div>
    );

  if (!token)
    return (
      <div className="ws-shell">
        <header className="ws-brand">
          <div className="ws-mark">
            <Heart size={20} />
          </div>
          <div>
            <strong>Wedding Studio</strong>
            <span>独立婚礼 MV 工作区</span>
          </div>
        </header>
        <section className="ws-card">
          <h1>婚礼 MV 项目需要访问令牌</h1>
          <p>请使用衣橱账号进入并创建项目，或通过新人邀请链接接受邀请。</p>
        </section>
      </div>
    );

  const participant = project?.participants.find((p) => p.person_key === "B");
  const canOwner = !isParticipant;
  const bothConsented =
    !!project?.participants.some(
      (p) => p.person_key === "A" && p.consent_status === "active",
    ) &&
    !!project?.participants.some(
      (p) => p.person_key === "B" && p.consent_status === "active",
    );
  const providerList = consentProviders(capabilities);
  const uncertain =
    creationJobs.some((job) => uncertainOutcome(job.error_code)) ||
    jobs.some((job) => uncertainOutcome(job.error_code));
  const creationActive = creationJobs.some(
    (job) => job.status === "queued" || job.status === "processing",
  );
  const queueStory = () => {
    if (uncertain || creationActive) return;
    void act(
      () =>
        createWeddingStoryJob(
          token,
          projectId!,
          project!.version,
          draft.story_text,
        ),
      () => void refresh(),
      "故事任务已排队；原文保留在本地，LIVE 可能收费",
    );
  };
  const queueFrames = () => {
    if (uncertain || creationActive || !storyboard) return;
    void act(
      () =>
        createWeddingFrameJob(
          token,
          projectId!,
          storyboard!.version,
          storyboard!.shots
            .filter((shot) => shot.shot_type === "photo")
            .map((shot) => ({
              shot_id: shot.id,
              asset_id: frameChoices[shot.id] || shot.frame_asset_id || "",
            })),
        ),
      () => void refresh(),
      "关键帧任务已排队，LIVE 按实际缺少镜头计费",
    );
  };
  const currentRender = selectedRenderId
    ? renders.find((render) => render.id === selectedRenderId)
    : renders[0];
  const canPreview =
    bothConsented &&
    !!currentRender &&
    !currentRender.invalidated_at &&
    currentRender.project_version === project?.version &&
    ["ready", "approved"].includes(currentRender.status);
  const previewId = currentRender?.preview_asset_id;
  const previewApprovedByA =
    !!previewId &&
    !!currentRender?.identity_reviews.some(
      (review) =>
        review.person_key === "A" &&
        review.asset_id === previewId &&
        review.result === "approved",
    );
  const previewApprovedByB =
    !!previewId &&
    !!currentRender?.identity_reviews.some(
      (review) =>
        review.person_key === "B" &&
        review.asset_id === previewId &&
        review.result === "approved",
    );
  const frameAssignments =
    storyboard?.shots
      .map((shot) => ({
        shot_id: shot.id,
        asset_id: frameChoices[shot.id] || shot.frame_asset_id || "",
      }))
      .filter((x) => !!x.asset_id) ?? [];
  const selectedShots =
    storyboard?.shots.filter(
      (shot) => !!shot.selected_candidate_id || !!shot.selected_asset_id,
    ) ?? [];
  const dynamicCount = selectedShots.filter((shot) => {
    const candidate = candidates.find(
      (c) => c.id === shot.selected_candidate_id,
    );
    const asset = assets.find(
      (a) => a.id === (candidate?.asset_id || shot.selected_asset_id),
    );
    return asset?.mime.startsWith("video/") ?? false;
  }).length;
  const photoCount = selectedShots.filter((shot) => {
    const candidate = candidates.find(
      (c) => c.id === shot.selected_candidate_id,
    );
    const asset = assets.find(
      (a) => a.id === (candidate?.asset_id || shot.selected_asset_id),
    );
    return asset?.mime.startsWith("image/") ?? false;
  }).length;
  const shotTotal = storyboard?.shots.length ?? 0;

  return (
    <div className="ws-shell">
      <header className="ws-topbar">
        <div className="ws-brand">
          <div className="ws-mark">
            <Heart size={20} />
          </div>
          <div>
            <strong>Wedding Studio</strong>
            <span>新人共同创作 · 私密项目空间</span>
          </div>
        </div>
        <div className="ws-topActions">
          {ownerToken &&
            projectId &&
            sessionStorage.getItem(participantKey(projectId)) && (
              <button
                type="button"
                className="ws-secondary"
                onClick={() => onRoleChange?.(!isParticipant)}
              >
                {isParticipant ? "切换项目所有者" : "切换 B 方本人"}
              </button>
            )}
          {project && <span className="ws-projectBadge">{project.title}</span>}
          {canOwner && projectId && (
            <>
              <button
                type="button"
                className="ws-secondary"
                onClick={returnToProjectList}
              >
                项目列表 / 新建
              </button>
              <button
                type="button"
                className="ws-danger"
                onClick={deleteProject}
                disabled={busy}
              >
                删除项目
              </button>
            </>
          )}
          <button
            type="button"
            className="ws-secondary"
            onClick={() => void refresh()}
            disabled={busy}
          >
            <RefreshCw size={16} />
            刷新项目
          </button>
        </div>
      </header>
      <main className="ws-main">
        <div className="ws-hero">
          <div>
            <p className="ws-eyebrow">婚礼影像制作</p>
            <h1>把你们的故事，做成一支共同确认的 MV</h1>
            <p>
              照片、故事与成片都由新人确认。当前工作区会如实显示素材与生成服务状态。
            </p>
          </div>
          <div className="ws-heroIcon">
            <Clapperboard size={34} />
          </div>
        </div>
        {error && (
          <div className="ws-error ws-alert" role="alert">
            <CircleAlert size={18} />
            <span>
              后端返回：<code>{error}</code>
            </span>
          </div>
        )}
        {notice && (
          <div className="ws-notice" role="status">
            {notice}
          </div>
        )}
        <div className="ws-statusGrid">
          <div className="ws-statusCard ws-demo">
            <strong>
              {capabilities?.demo_only
                ? "DEMO ONLY / NO AI MOTION"
                : capabilities?.mode === "disabled"
                  ? "NO AI MOTION / SERVICES DISABLED"
                  : "AI MOTION CAPABILITY GATE"}
            </strong>
            <span>
              {capabilities?.demo_only
                ? "当前为演示模式；不会伪造 AI 动态镜头或成片。"
                : capabilities?.real_export_available
                  ? "后端报告具备导出条件；仍只显示后端实际产出的素材。"
                  : "动态视频和导出仅在后端确认可用并真实返回素材后显示。"}
            </span>
          </div>
          <div className="ws-statusCard">
            <strong>服务状态</strong>
            <span>
              {capabilities
                ? `故事 ${capabilities.providers.story?.available ? "可用" : "不可用"} · 人像视频 ${capabilities.providers.character_video?.available ? "可用" : "不可用"} · 场景视频 ${capabilities.providers.scene_video?.available ? "可用" : "不可用"} · FFmpeg ${capabilities.ffmpeg_available ? "可用" : "不可用"} · 授权音乐 ${capabilities.licensed_music_available ? "可用" : "不可用"}`
                : "读取服务状态中…"}
            </span>
          </div>
          <div className="ws-statusCard">
            <strong>预算上限</strong>
            <span>
              {project
                ? `¥${(project.budget_limit_fen / 100).toFixed(2)} · 已预留 ¥${(project.budget_reserved_fen / 100).toFixed(2)} · 已用 ¥${(project.budget_spent_fen / 100).toFixed(2)}`
                : "创建项目后显示"}
            </span>
          </div>
          <div className="ws-statusCard">
            <strong>镜头构成 · 已选实际素材</strong>
            <span>
              {shotTotal
                ? `动态 ${dynamicCount}/${selectedShots.length} · 照片 ${photoCount}/${selectedShots.length} · 待选 ${shotTotal - selectedShots.length}`
                : "提交分镜后显示实际选择比例"}
            </span>
          </div>
        </div>
        {!projectId && canOwner && (
          <section className="ws-card">
            <div className="ws-sectionHead">
              <div>
                <p className="ws-eyebrow">新建项目</p>
                <h2>开始共同创作</h2>
              </div>
              <span className="ws-step">01</span>
            </div>
            <div className="ws-createGrid">
              <label>
                项目名称
                <input
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  maxLength={120}
                />
              </label>
              <label>
                你的称呼
                <input
                  value={ownerName}
                  onChange={(e) => setOwnerName(e.target.value)}
                  maxLength={20}
                />
              </label>
              <label>
                视觉风格
                <select
                  value={style}
                  onChange={(e) => {
                    const next = e.target.value as WeddingStyle;
                    setStyle(next);
                    setDraft(templateDraft(1, next));
                  }}
                >
                  <option value="warm_documentary">温暖纪实</option>
                  <option value="romantic_film">浪漫电影感</option>
                  <option value="fresh_illustration">清新插画</option>
                </select>
              </label>
            </div>
            <button
              className="ws-primary"
              disabled={busy || !ownerName.trim()}
              onClick={() =>
                void act(
                  () =>
                    createWeddingProject(ownerToken!, {
                      title: title.trim(),
                      owner_name: ownerName.trim(),
                      style,
                    }),
                  (p) => {
                    setProjects((xs) => [p, ...xs]);
                    setActiveProject(p.id);
                  },
                  "项目已创建",
                )
              }
            >
              <Sparkles size={17} />
              创建项目
            </button>
            {projects.length > 0 && (
              <div className="ws-existing">
                <h3>继续编辑项目</h3>
                {projects.map((p) => (
                  <button
                    type="button"
                    className="ws-projectRow"
                    key={p.id}
                    onClick={() => setActiveProject(p.id)}
                  >
                    <span>{p.title}</span>
                    <span>
                      第 {p.version} 版 · {p.status}
                    </span>
                  </button>
                ))}
              </div>
            )}
          </section>
        )}
        {projectId && project && (
          <>
            <section className="ws-card">
              <div className="ws-sectionHead">
                <div>
                  <p className="ws-eyebrow">01 · 双方授权</p>
                  <h2>先确认素材使用范围</h2>
                </div>
                <span className="ws-step">
                  {
                    project.participants.filter(
                      (p) => p.consent_status === "active",
                    ).length
                  }
                  /2
                </span>
              </div>
              <p>
                只有本人可以上传和确认涉及自己的肖像镜头。双方均可随时停止项目。当前声明的处理方：
                {providerList.join("、")}。
              </p>
              <div className="ws-consentRow">
                <span>
                  <b>
                    A ·{" "}
                    {project.participants.find((p) => p.person_key === "A")
                      ?.display_name ??
                      ownerName ??
                      "新人 A"}
                  </b>
                  <small>
                    授权：
                    {project.participants.find((p) => p.person_key === "A")
                      ?.consent_status ?? "待确认"}
                  </small>
                </span>
                {canOwner &&
                  project.participants.find((p) => p.person_key === "A")
                    ?.consent_status !== "active" && (
                    <button
                      className="ws-secondary"
                      disabled={busy}
                      onClick={() =>
                        void act(
                          () =>
                            confirmWeddingOwnerConsent(
                              token,
                              projectId,
                              providerList,
                            ),
                          () => void refresh(),
                        )
                      }
                    >
                      <Check size={16} />
                      我已阅读并同意
                    </button>
                  )}
                {canOwner &&
                  project.participants.find((p) => p.person_key === "A")
                    ?.consent_status === "active" && (
                    <button
                      className="ws-danger"
                      disabled={busy}
                      onClick={revokeMyConsent}
                    >
                      撤回我的授权
                    </button>
                  )}
              </div>
              <div className="ws-consentRow">
                <span>
                  <b>B · {participant?.display_name ?? "另一位新人"}</b>
                  <small>授权：{participant?.consent_status ?? "待邀请"}</small>
                </span>
                {isParticipant && participant?.consent_status === "active" && (
                  <button
                    className="ws-danger"
                    disabled={busy}
                    onClick={revokeMyConsent}
                  >
                    撤回我的授权
                  </button>
                )}
                {canOwner && (
                  <div className="ws-inlineForm">
                    <label htmlFor="ws-invite-name">邀请称呼</label>
                    <input
                      id="ws-invite-name"
                      value={inviteName}
                      onChange={(e) => setInviteName(e.target.value)}
                      placeholder="另一位新人的称呼"
                    />
                    <button
                      className="ws-secondary"
                      disabled={busy || !inviteName.trim()}
                      onClick={() =>
                        void act(
                          () =>
                            createWeddingInvite(
                              token,
                              projectId,
                              inviteName.trim(),
                              providerList,
                            ),
                          (result) =>
                            setInviteUrl(
                              `${window.location.origin}${window.location.pathname}?wedding_project=${encodeURIComponent(projectId)}&wedding_invite=${encodeURIComponent(result.invite_token)}`,
                            ),
                          "邀请链接已创建",
                        )
                      }
                    >
                      <Send size={16} />
                      生成单次邀请
                    </button>
                  </div>
                )}
              </div>
              {inviteUrl && (
                <label className="ws-inviteLink">
                  单次邀请链接
                  <input
                    readOnly
                    value={inviteUrl}
                    onFocus={(e) => e.currentTarget.select()}
                  />
                  <button
                    className="ws-secondary"
                    type="button"
                    onClick={() =>
                      void navigator.clipboard?.writeText(inviteUrl)
                    }
                  >
                    <Copy size={15} />
                    复制
                  </button>
                </label>
              )}
            </section>
            <section className="ws-card">
              <div className="ws-sectionHead">
                <div>
                  <p className="ws-eyebrow">02 · 本人照片</p>
                  <h2>分别上传本人照片</h2>
                </div>
                <span className="ws-step">
                  {assets.filter((a) => a.asset_type === "photo").length}
                </span>
              </div>
              <div className="ws-uploadGrid">
                {(isParticipant ? (["B"] as const) : (["A"] as const)).map(
                  (person) => (
                    <label className="ws-uploadBox" key={person}>
                      <Upload size={19} />
                      <b>{person} 方上传</b>
                      <span>JPEG / PNG / WebP，最多 20 MB</span>
                      <input
                        aria-label={`${person} 方上传本人照片`}
                        type="file"
                        accept="image/jpeg,image/png,image/webp"
                        multiple
                        onChange={(e) => {
                          const files = Array.from(e.target.files ?? []);
                          void act(
                            async () => {
                              const scopeEpoch = epoch.current;
                              for (const file of files) {
                                if (
                                  !alive.current ||
                                  epoch.current !== scopeEpoch
                                )
                                  throw new Error("SCOPE_CHANGED");
                                await uploadWeddingPhoto(
                                  token,
                                  projectId,
                                  file,
                                  person,
                                );
                              }
                              await refresh();
                              return true;
                            },
                            undefined,
                            "照片已安全上传",
                          );
                          e.currentTarget.value = "";
                        }}
                      />
                    </label>
                  ),
                )}
              </div>
              <div className="ws-assetGrid">
                {assets
                  .filter((a) => a.asset_type === "photo")
                  .map((a) => (
                    <article className="ws-assetCard" key={a.id}>
                      <PrivateMedia token={token} assetId={a.id} />
                      <span>
                        {a.person_key} 方 ·{" "}
                        {Math.max(1, Math.round(a.size_bytes / 1024))} KB
                      </span>
                    </article>
                  ))}
              </div>
              <PhotoReadiness
                readiness={readiness}
                ownPerson={isParticipant ? "B" : "A"}
                token={token}
                projectId={projectId}
                version={project.version}
                run={act}
                refresh={refresh}
              />
            </section>
            <section className="ws-card">
              <div className="ws-sectionHead">
                <div>
                  <p className="ws-eyebrow">03 · 故事与分镜</p>
                  <h2>新人填写与确认</h2>
                </div>
                <span className="ws-step">{storyboard?.status ?? "草稿"}</span>
              </div>
              <p className="ws-callout">
                故事原文由新人提供；AI 结果需新人确认事实与分镜。DEMO
                是模板流程，LIVE 使用可用服务并可能收费。
              </p>
              {canOwner && (
                <>
                  <p role="status">{editor.status}</p>
                  {editor.dirty && storyboard && (
                    <details>
                      <summary>比较服务器草稿 / AI 编排结果</summary>
                      <p>
                        先用“刷新项目”读取最新版本。你的本地编辑仍保留在上方状态对应的编辑器中。
                      </p>
                      <pre className="ws-serverDraft">
                        {storyboard.story_text}
                      </pre>
                      <p>
                        {storyboard.facts
                          .map((fact) => `${fact.source_field}：${fact.text}`)
                          .join("；")}
                      </p>
                    </details>
                  )}
                  {editor.dirty && (
                    <button
                      type="button"
                      className="ws-secondary"
                      onClick={() => {
                        if (
                          window.confirm("放弃本地未保存编辑，载入服务器草稿？")
                        )
                          editor.reload();
                      }}
                    >
                      比较后载入服务器草稿
                    </button>
                  )}
                  <button
                    className="ws-primary"
                    disabled={
                      busy ||
                      creationActive ||
                      uncertain ||
                      !bothConsented ||
                      !capabilities?.providers.story?.available ||
                      !draft.story_text.trim() ||
                      (!!storyboard && storyboard.status !== "draft")
                    }
                    onClick={queueStory}
                  >
                    AI 编排故事
                  </button>
                </>
              )}
              <fieldset
                disabled={
                  (!!storyboard && storyboard.status !== "draft") ||
                  isParticipant
                }
                className="ws-editor"
              >
                <label>
                  你们的爱情故事
                  <textarea
                    rows={4}
                    value={draft.story_text}
                    onChange={(e) =>
                      setDraft({ ...draft, story_text: e.target.value })
                    }
                    placeholder="写下相遇、重要转折，以及想对彼此说的话"
                  />
                </label>
                <div className="ws-facts">
                  <h3>故事事实</h3>
                  {draft.facts.map((fact, index) => (
                    <div className="ws-factRow" key={index}>
                      <label>
                        主题
                        <input
                          value={fact.source_field}
                          onChange={(e) =>
                            setDraft({
                              ...draft,
                              facts: draft.facts.map((f, i) =>
                                i === index
                                  ? { ...f, source_field: e.target.value }
                                  : f,
                              ),
                            })
                          }
                        />
                      </label>
                      <label>
                        事实
                        <input
                          value={fact.text}
                          onChange={(e) =>
                            setDraft({
                              ...draft,
                              facts: draft.facts.map((f, i) =>
                                i === index
                                  ? { ...f, text: e.target.value }
                                  : f,
                              ),
                            })
                          }
                        />
                      </label>
                    </div>
                  ))}
                  <button
                    type="button"
                    className="ws-textButton"
                    onClick={() =>
                      setDraft({
                        ...draft,
                        facts: [...draft.facts, { source_field: "", text: "" }],
                      })
                    }
                  >
                    ＋ 添加事实
                  </button>
                </div>
                <div className="ws-templateRow">
                  <h3>可编辑模板：90 秒 / 18 镜头 / 每镜头 5 秒</h3>
                  <span>8 人像 · 4 场景 · 6 照片</span>
                  <button
                    type="button"
                    className="ws-secondary"
                    onClick={() =>
                      setDraft(templateDraft(project.version, project.style))
                    }
                  >
                    应用模板
                  </button>
                </div>
                <div className="ws-shotEditor">
                  {draft.shots.map((shot, index) => (
                    <div className="ws-shotEdit" key={shot.shot_index}>
                      <span className="ws-shotNumber">
                        {String(shot.shot_index).padStart(2, "0")}
                      </span>
                      <label>
                        镜头类型
                        <select
                          value={shot.shot_type}
                          onChange={(e) =>
                            setDraft({
                              ...draft,
                              shots: draft.shots.map((s, i) =>
                                i === index
                                  ? {
                                      ...s,
                                      shot_type: e.target
                                        .value as typeof s.shot_type,
                                    }
                                  : s,
                              ),
                            })
                          }
                        >
                          <option value="person_ai">
                            人像镜头（候选生成）
                          </option>
                          <option value="scene_ai">氛围场景镜头</option>
                          <option value="photo">照片镜头</option>
                        </select>
                      </label>
                      <label>
                        时长（毫秒）
                        <input
                          type="number"
                          min={1000}
                          max={10000}
                          step={500}
                          value={shot.duration_ms}
                          onChange={(e) =>
                            setDraft({
                              ...draft,
                              shots: draft.shots.map((s, i) =>
                                i === index
                                  ? {
                                      ...s,
                                      duration_ms: Number(e.target.value),
                                    }
                                  : s,
                              ),
                            })
                          }
                        />
                      </label>
                      <label>
                        涉及本人
                        <select
                          value={shot.person_keys.join(",")}
                          onChange={(e) =>
                            setDraft({
                              ...draft,
                              shots: draft.shots.map((s, i) =>
                                i === index
                                  ? {
                                      ...s,
                                      person_keys: e.target.value
                                        ? (e.target.value.split(",") as Array<
                                            "A" | "B"
                                          >)
                                        : [],
                                    }
                                  : s,
                              ),
                            })
                          }
                        >
                          <option value="">无</option>
                          <option value="A">A</option>
                          <option value="B">B</option>
                          <option value="A,B">A + B</option>
                        </select>
                      </label>
                      <label className="ws-description">
                        画面说明
                        <input
                          value={shot.description}
                          onChange={(e) =>
                            setDraft({
                              ...draft,
                              shots: draft.shots.map((s, i) =>
                                i === index
                                  ? { ...s, description: e.target.value }
                                  : s,
                              ),
                            })
                          }
                        />
                      </label>
                      <label className="ws-description">
                        字幕
                        <input
                          value={shot.subtitle}
                          onChange={(e) =>
                            setDraft({
                              ...draft,
                              shots: draft.shots.map((s, i) =>
                                i === index
                                  ? { ...s, subtitle: e.target.value }
                                  : s,
                              ),
                            })
                          }
                        />
                      </label>
                    </div>
                  ))}
                </div>
              </fieldset>
              {canOwner && (
                <div className="ws-actions">
                  <button
                    className="ws-secondary"
                    disabled={
                      busy ||
                      (!!storyboard && storyboard.status !== "draft") ||
                      draft.shots.length > 18 ||
                      draft.shots.some(
                        (s) => s.duration_ms > 10000 || s.duration_ms < 1000,
                      ) ||
                      draft.shots.reduce((a, s) => a + s.duration_ms, 0) !==
                        project.target_duration_ms
                    }
                    onClick={() =>
                      void act(
                        () => editor.flush(),
                        (s) => {
                          if (s) setStoryboard(s);
                        },
                        "分镜草稿已保存",
                      )
                    }
                  >
                    保存分镜草稿
                  </button>
                  <button
                    className="ws-primary"
                    disabled={
                      busy || !storyboard || storyboard.status !== "draft"
                    }
                    onClick={() =>
                      storyboard &&
                      void act(
                        async () => {
                          const saved = await editor.flush();
                          if (!saved) throw new Error("请先保存完整草稿");
                          return approveWeddingStoryboard(
                            token,
                            projectId,
                            saved.version,
                            editor.getFingerprint(),
                          );
                        },
                        (s) => {
                          setStoryboard(s);
                          void refresh();
                        },
                        "分镜已确认",
                      )
                    }
                  >
                    新人确认分镜
                  </button>
                  <span className="ws-totalTime">
                    合计{" "}
                    {draft.shots.reduce((a, s) => a + s.duration_ms, 0) / 1000}{" "}
                    / {project.target_duration_ms / 1000} 秒
                  </span>
                </div>
              )}
            </section>
            {storyboard && (
              <section className="ws-card">
                <div className="ws-sectionHead">
                  <div>
                    <p className="ws-eyebrow">04 · 关键帧与审核</p>
                    <h2>逐镜选择候选、照片回退与本人审核</h2>
                  </div>
                  <span className="ws-step">{storyboard.shots.length} 镜</span>
                </div>
                <p>
                  所有素材通过授权 API
                  读取。每位新人只看到并确认涉及自己的镜头。
                </p>
                {canOwner && (
                  <div className="ws-actions">
                    <button
                      className="ws-primary"
                      disabled={
                        busy ||
                        creationActive ||
                        uncertain ||
                        storyboard.status !== "approved" ||
                        !bothConsented ||
                        !capabilities?.providers.frame?.available ||
                        (!readiness?.demo_bypass && !readiness?.ready) ||
                        storyboard.shots
                          .filter((shot) => shot.shot_type === "photo")
                          .some(
                            (shot) =>
                              !(frameChoices[shot.id] || shot.frame_asset_id),
                          )
                      }
                      onClick={queueFrames}
                    >
                      AI 制作关键帧
                    </button>
                    <span>
                      为全部照片镜头选择原图，AI
                      镜头自动制作。更换主参考后先核对重做费用。
                    </span>
                  </div>
                )}
                {storyboard.shots.map((shot) => (
                  <ShotReview
                    key={shot.id}
                    shot={shot}
                    candidates={candidates.filter((c) => c.shot_id === shot.id)}
                    assets={assets}
                    token={token}
                    owner={canOwner}
                    version={storyboard.version}
                    projectId={projectId}
                    busy={busy}
                    generationBlocked={uncertain}
                    run={act}
                    refresh={refresh}
                    frameChoice={
                      frameChoices[shot.id] || shot.frame_asset_id || ""
                    }
                    onFrameChoice={(assetId) =>
                      setFrameChoices((current) => ({
                        ...current,
                        [shot.id]: assetId,
                      }))
                    }
                  />
                ))}
                {canOwner && (
                  <div className="ws-actions">
                    <button
                      className="ws-secondary"
                      disabled={
                        busy ||
                        frameAssignments.length !== storyboard.shots.length ||
                        creationActive
                      }
                      onClick={() =>
                        void act(
                          () =>
                            approveWeddingFrames(
                              token,
                              projectId,
                              storyboard.version,
                              frameAssignments,
                            ),
                          (s) => setStoryboard(s),
                          "关键帧已提交审核",
                        )
                      }
                    >
                      确认已选关键帧
                    </button>
                    <button
                      className="ws-primary"
                      disabled={busy || uncertain}
                      onClick={() =>
                        void act(
                          async () => {
                            const startEpoch = epoch.current;
                            const quote = await createWeddingQuote(
                              token,
                              projectId,
                            );
                            if (!alive.current || epoch.current !== startEpoch)
                              throw new Error("SCOPE_CHANGED");
                            return createWeddingGenerationJob(
                              token,
                              projectId,
                              quote.id,
                              storyboard.version,
                            );
                          },
                          async () => {
                            await refresh();
                          },
                          "生成任务已提交；进度取自后端状态",
                        )
                      }
                    >
                      <Sparkles size={16} />
                      提交生成任务
                    </button>
                    <span className="ws-totalTime">
                      关键帧 {frameAssignments.length}/{storyboard.shots.length}
                    </span>
                  </div>
                )}
              </section>
            )}
            <section className="ws-card">
              <div className="ws-sectionHead">
                <div>
                  <p className="ws-eyebrow">05 · 生成与预算</p>
                  <h2>真实任务状态</h2>
                </div>
                <span className="ws-step">{jobs.length} 项</span>
              </div>
              {canOwner && (
                <CreationProgress
                  jobs={creationJobs}
                  token={token}
                  projectId={projectId}
                  run={act}
                  refresh={refresh}
                  retry={(kind) =>
                    kind === "story" ? queueStory() : queueFrames()
                  }
                />
              )}
              <p>
                预留与用量估算分别显示；权威账单由运营核对。结果未知时不会盲目重新提交。
              </p>
              {jobs.length ? (
                jobs.map((job) => (
                  <div className="ws-job" key={job.id}>
                    <div>
                      <b>
                        {job.status} · {job.progress_stage}
                      </b>
                      <span>
                        {job.completed_shots}/{job.total_shots} 镜 · 预留 ¥
                        {(job.reserved_amount_fen / 100).toFixed(2)} · 用量估算
                        ¥{(job.actual_cost_fen / 100).toFixed(2)}
                      </span>
                    </div>
                    {job.error_code && <code>{job.error_code}</code>}
                  </div>
                ))
              ) : (
                <p>
                  尚无生成任务。生成服务不可用、授权不足或预算超限时，会原样显示后端原因。
                </p>
              )}
            </section>
            {canOwner && (
              <section className="ws-card">
                <MusicChooser
                  tracks={music}
                  token={token}
                  projectId={projectId}
                  run={act}
                  onSelection={setMusicId}
                />
                <button
                  className="ws-secondary"
                  disabled={busy || !bothConsented}
                  onClick={() =>
                    void act(
                      () =>
                        createWeddingRender(token, projectId, project.version),
                      (result) => {
                        setSelectedRenderId(result.id);
                        void refresh();
                      },
                      "预览渲染已排队",
                    )
                  }
                >
                  <Video size={16} />
                  创建首版/当前分镜预览
                </button>
                <p>
                  素材期限：{project.retention_expires_at || "读取中"} ·
                  成片下载 {project.final_download_count ?? 0}/
                  {project.final_download_limit ?? 10} 次 · 预览流量{" "}
                  {Math.round(
                    (project.preview_bytes_served ?? 0) / 1024 / 1024,
                  )}{" "}
                  MB。实际读取文件计数。
                </p>
              </section>
            )}
            {renders.length > 0 && (
              <section className="ws-card">
                <div className="ws-sectionHead">
                  <div>
                    <p className="ws-eyebrow">06 · 预览、双方验收与导出</p>
                    <h2>成片需要双方本人验收</h2>
                  </div>
                  <span className="ws-step">{currentRender?.status}</span>
                </div>
                <RenderHistory
                  renders={renders}
                  selected={currentRender?.id || ""}
                  currentVersion={project.version}
                  onSelect={(id) => {
                    setSelectedRenderId(id);
                    setSubtitleEdits({});
                    setMusicId("");
                  }}
                />
                {currentRender?.error_code && (
                  <p role="alert">
                    {currentRender.error_code} · 尝试{" "}
                    {currentRender.attempts ?? 0}/3
                  </p>
                )}
                {!canPreview && (
                  <p>
                    此版本尚未就绪、已失效或属于旧内容；仅保留历史记录，无法播放或验收。
                  </p>
                )}
                {canOwner && currentRender && (
                  <div className="ws-subtitles">
                    <h3>预览后调整字幕</h3>
                    <p>
                      每镜头最多两行，每行最多 18
                      个全角字符。修改后创建新成片，需双方重新验收。
                    </p>
                    {currentRender.timeline?.map((shot) => (
                      <label key={shot.shot_id}>
                        镜头 {shot.shot_index} 字幕
                        <textarea
                          rows={2}
                          value={subtitleEdits[shot.shot_id] ?? shot.subtitle}
                          onChange={(event) =>
                            setSubtitleEdits((edits) => ({
                              ...edits,
                              [shot.shot_id]: event.target.value,
                            }))
                          }
                        />
                      </label>
                    ))}
                    <div className="ws-actions">
                      <button
                        className="ws-secondary"
                        disabled={busy || !canPreview}
                        onClick={() =>
                          void act(
                            () =>
                              createWeddingRender(
                                token,
                                projectId,
                                project.version,
                                {
                                  source_render_id: currentRender.id,
                                  subtitles: subtitleEdits,
                                  ...(musicId
                                    ? { music_track_id: musicId }
                                    : {}),
                                },
                              ),
                            (result) => {
                              setSelectedRenderId(result.id);
                              setSubtitleEdits({});
                              void refresh();
                            },
                            "字幕/音乐新版本已排队；等待双方重新验收",
                          )
                        }
                      >
                        保存字幕/音乐为新版本
                      </button>
                      <button
                        className="ws-secondary"
                        disabled={busy || !canPreview}
                        onClick={() =>
                          void act(
                            () =>
                              restoreWeddingRender(
                                token,
                                projectId,
                                currentRender.id,
                                project.version,
                              ),
                            (result) => {
                              setSelectedRenderId(result.id);
                              setSubtitleEdits({});
                              void refresh();
                            },
                            "历史版本已复制为新渲染；需双方重新验收",
                          )
                        }
                      >
                        恢复所选版本
                      </button>
                    </div>
                  </div>
                )}
                {canPreview && currentRender?.preview_asset_id && (
                  <PrivateMedia
                    token={token}
                    assetId={currentRender.preview_asset_id}
                    video
                  />
                )}
                <div className="ws-job">
                  <b>
                    成片版本 {currentRender?.version} ·{" "}
                    {currentRender?.output_duration_ms
                      ? `${currentRender.output_duration_ms / 1000} 秒`
                      : "时长处理中"}
                  </b>
                  <span>
                    {currentRender?.identity_reviews
                      .map((r) => `${r.person_key} ${r.result}`)
                      .join(" · ") || "等待双方分别验收"}
                  </span>
                </div>
                {canPreview &&
                  currentRender?.preview_asset_id &&
                  !currentRender.identity_reviews.some(
                    (r) =>
                      r.person_key === (isParticipant ? "B" : "A") &&
                      r.asset_id === currentRender.preview_asset_id &&
                      r.result === "approved",
                  ) && (
                    <button
                      className="ws-secondary"
                      disabled={busy}
                      onClick={() =>
                        void act(
                          () =>
                            submitWeddingRenderReview(
                              token,
                              projectId,
                              currentRender.id,
                              currentRender.preview_asset_id!,
                              project.version,
                            ),
                          async () => {
                            await refresh();
                          },
                          "本人预览验收已提交",
                        )
                      }
                    >
                      <Check size={16} />
                      我确认这版预览
                    </button>
                  )}
                {canOwner && currentRender && (
                  <div className="ws-actions">
                    <button
                      className="ws-secondary"
                      disabled={
                        busy ||
                        !canPreview ||
                        !previewApprovedByA ||
                        !previewApprovedByB ||
                        currentRender.status !== "ready"
                      }
                      onClick={() =>
                        void act(
                          () =>
                            approveWeddingRender(
                              token,
                              projectId,
                              currentRender.id,
                            ),
                          async () => {
                            await refresh();
                          },
                          "双方验收完成",
                        )
                      }
                    >
                      确认双方验收
                    </button>
                    <button
                      className="ws-primary"
                      disabled={
                        busy ||
                        !canPreview ||
                        currentRender.status !== "approved"
                      }
                      onClick={() =>
                        void act(
                          async () => {
                            const scopeEpoch = epoch.current;
                            const result = await exportWeddingRender(
                              token,
                              projectId,
                              currentRender.id,
                            );
                            if (!alive.current || epoch.current !== scopeEpoch)
                              return result;
                            const blob = await fetchWeddingAssetBlob(
                              token,
                              result.asset_id,
                            );
                            if (!alive.current || epoch.current !== scopeEpoch)
                              return result;
                            const url = URL.createObjectURL(blob);
                            const link = document.createElement("a");
                            link.href = url;
                            link.download = `${project.title || "wedding-mv"}.mp4`;
                            link.click();
                            URL.revokeObjectURL(url);
                            return result;
                          },
                          undefined,
                          "已下载成片",
                        )
                      }
                    >
                      <ArrowDownToLine size={16} />
                      导出成片
                    </button>
                    <button
                      className="ws-secondary"
                      disabled={busy || !bothConsented}
                      onClick={() =>
                        void act(
                          () =>
                            createWeddingRender(
                              token,
                              projectId,
                              project.version,
                            ),
                          async () => {
                            await refresh();
                          },
                          "渲染请求已提交",
                        )
                      }
                    >
                      <Video size={16} />
                      请求预览渲染
                    </button>
                  </div>
                )}
              </section>
            )}
          </>
        )}
      </main>
    </div>
  );
}

function ShotReview({
  shot,
  candidates,
  assets,
  token,
  owner,
  version,
  projectId,
  busy,
  generationBlocked,
  run,
  refresh,
  frameChoice,
  onFrameChoice,
}: {
  shot: WeddingShot;
  candidates: WeddingCandidate[];
  assets: WeddingAsset[];
  token: string;
  owner: boolean;
  version: number;
  projectId: string;
  busy: boolean;
  generationBlocked: boolean;
  run: <T>(
    fn: () => Promise<T>,
    done?: (result: T) => void,
    success?: string,
  ) => Promise<T | undefined>;
  refresh: () => Promise<void>;
  frameChoice: string;
  onFrameChoice: (assetId: string) => void;
}) {
  const scopeAlive = useRef(true);
  useEffect(() => {
    scopeAlive.current = true;
    return () => {
      scopeAlive.current = false;
    };
  }, []);
  const ownKey = owner ? "A" : "B";
  const frameOptions = assets.filter(
    (a) =>
      a.asset_type === "photo" &&
      (shot.person_keys.length === 0 ||
        shot.person_keys.includes(a.person_key as "A" | "B")),
  );
  const photoOptions = assets.filter(
    (a) =>
      a.asset_type === "photo" &&
      shot.person_keys.length === 1 &&
      a.person_key === shot.person_keys[0],
  );
  const frame = assets.find((a) => a.id === frameChoice);
  const selection = candidates.find((c) => c.id === shot.selected_candidate_id);
  const reviewAssetId =
    selection?.asset_id || shot.selected_asset_id || frameChoice;
  const needsMyReview = shot.person_keys.includes(ownKey) && !!reviewAssetId;
  return (
    <article className="ws-shotCard">
      <div className="ws-shotTitle">
        <span className="ws-shotNumber">
          {String(shot.shot_index).padStart(2, "0")}
        </span>
        <div>
          <b>{shot.description}</b>
          <span>
            {shot.shot_type} · {shot.duration_ms / 1000}s ·{" "}
            {shot.person_keys.join("+") || "氛围"}
          </span>
        </div>
        <span className="ws-shotStatus">{shot.status}</span>
      </div>
      <p>{shot.subtitle || "（无字幕）"}</p>
      {owner && (
        <label className="ws-fallback">
          关键帧照片
          <select
            value={frameChoice}
            onChange={(e) => onFrameChoice(e.target.value)}
          >
            <option value="">选择照片…</option>
            {frameOptions.map((a) => (
              <option key={a.id} value={a.id}>
                {a.person_key} 方照片 · {a.id.slice(0, 8)}
              </option>
            ))}
          </select>
        </label>
      )}
      {frame && (
        <div className="ws-currentFrame">
          <PrivateMedia token={token} assetId={frame.id} />
          <small>当前关键帧 · {frame.person_key}</small>
        </div>
      )}
      <div className="ws-candidateList">
        {candidates.map((c) => {
          const approvedBy = shot.identity_reviews
            .filter(
              (review) =>
                review.asset_id === c.asset_id && review.result === "approved",
            )
            .map((review) => review.person_key);
          const allPeopleApproved = shot.person_keys.every((person) =>
            approvedBy.includes(person),
          );
          const ownApproved = shot.identity_reviews.some(
            (review) =>
              review.person_key === ownKey &&
              review.asset_id === c.asset_id &&
              review.result === "approved",
          );
          const ownReview = shot.identity_reviews.find(
            (review) =>
              review.person_key === ownKey && review.asset_id === c.asset_id,
          );
          return (
            <div className="ws-candidate" key={c.id}>
              <PrivateMedia
                token={token}
                assetId={c.asset_id}
                video={c.asset.mime.startsWith("video/")}
                videoLabel="镜头候选私密视频"
              />
              <div>
                <b>
                  {c.provider} · {c.model_version || "未提供版本"}
                </b>
                <span>
                  {c.review_result === "rejected" ? "候选已拒绝" : "本人审核"} ·
                  ¥{(c.actual_cost_fen / 100).toFixed(2)}
                </span>
                <small>
                  {shot.person_keys.length
                    ? shot.person_keys
                        .map(
                          (person) =>
                            `${person}：${approvedBy.includes(person) ? "已确认" : "待确认"}`,
                        )
                        .join(" · ")
                    : "氛围镜头无需本人审核"}
                </small>
                {c.quality_flags.length > 0 && (
                  <small>{c.quality_flags.join(" / ")}</small>
                )}
              </div>
              {shot.person_keys.includes(ownKey) && (
                <>
                  <button
                    className="ws-secondary"
                    disabled={busy || ownApproved}
                    onClick={() =>
                      void run(
                        () =>
                          submitWeddingShotReview(
                            token,
                            shot.id,
                            c.asset_id,
                            version,
                            "approved",
                          ),
                        async () => refresh(),
                        "本人候选审核已提交",
                      )
                    }
                  >
                    {ownApproved ? "本人已确认" : "我确认候选"}
                  </button>
                  <button
                    className="ws-secondary"
                    disabled={busy || ownReview?.result === "rejected"}
                    onClick={() =>
                      void run(
                        () =>
                          submitWeddingShotReview(
                            token,
                            shot.id,
                            c.asset_id,
                            version,
                            "rejected",
                          ),
                        async () => refresh(),
                        "已记录本人拒绝意见",
                      )
                    }
                  >
                    {ownReview?.result === "rejected"
                      ? "本人已拒绝"
                      : "拒绝候选"}
                  </button>
                </>
              )}
              {owner && (
                <button
                  className="ws-secondary"
                  disabled={
                    busy || c.review_result === "rejected" || !allPeopleApproved
                  }
                  onClick={() =>
                    void run(
                      () =>
                        selectWeddingCandidate(
                          token,
                          projectId,
                          shot.id,
                          c.id,
                          version,
                        ),
                      async () => refresh(),
                      "已选择镜头候选",
                    )
                  }
                >
                  选用
                </button>
              )}
            </div>
          );
        })}
      </div>
      {photoOptions.length > 0 && owner && (
        <label className="ws-fallback">
          照片回退
          <select
            defaultValue=""
            onChange={(e) => {
              if (e.target.value)
                void run(
                  () =>
                    acceptWeddingPhotoFallback(
                      token,
                      projectId,
                      shot.id,
                      e.target.value,
                      version,
                    ),
                  async () => refresh(),
                  "已选择照片回退",
                );
            }}
          >
            <option value="">选择本人照片…</option>
            {photoOptions.map((a) => (
              <option key={a.id} value={a.id}>
                {a.person_key} 方照片 · {a.id.slice(0, 8)}
              </option>
            ))}
          </select>
        </label>
      )}
      {needsMyReview && (
        <>
          <button
            className="ws-secondary"
            disabled={
              busy ||
              shot.identity_reviews.some(
                (r) =>
                  r.person_key === ownKey &&
                  r.asset_id === reviewAssetId &&
                  r.result === "approved",
              )
            }
            onClick={() =>
              void run(
                () =>
                  submitWeddingShotReview(
                    token,
                    shot.id,
                    reviewAssetId,
                    version,
                    "approved",
                  ),
                async () => refresh(),
                "本人镜头审核已提交",
              )
            }
          >
            <Check size={15} />
            我确认此镜头
          </button>
          <button
            className="ws-secondary"
            disabled={
              busy ||
              shot.identity_reviews.some(
                (r) =>
                  r.person_key === ownKey &&
                  r.asset_id === reviewAssetId &&
                  r.result === "rejected",
              )
            }
            onClick={() =>
              void run(
                () =>
                  submitWeddingShotReview(
                    token,
                    shot.id,
                    reviewAssetId,
                    version,
                    "rejected",
                  ),
                async () => refresh(),
                "已记录本人拒绝意见",
              )
            }
          >
            拒绝此镜头
          </button>
        </>
      )}
      {owner && (
        <button
          className="ws-secondary"
          disabled={busy || generationBlocked}
          onClick={() =>
            void run(
              async () => {
                const quote = await createWeddingQuote(
                  token,
                  projectId,
                  shot.id,
                );
                if (!scopeAlive.current) throw new Error("SCOPE_CHANGED");
                return regenerateWeddingShot(
                  token,
                  projectId,
                  shot.id,
                  quote.id,
                  version,
                );
              },
              async () => refresh(),
              "单镜头重做任务已提交",
            )
          }
        >
          <RefreshCw size={15} />
          重做此镜头
        </button>
      )}
    </article>
  );
}

function message(error: unknown) {
  return error instanceof Error ? error.message : String(error);
}
