import type {
  AuthResponse,
  AnalysisTask,
  CandidateV2,
  DecisionFeedback,
  Garment,
  Occasion,
  Outfit,
  PurchaseCandidate,
  ShoppingRecommendationRun,
  ShoppingRecommendationTarget,
  ShoppingRecommendationItem,
  UploadSession,
  UserPreference,
  WearEvent,
  WardrobeReport,
  Weather,
  WeddingAsset,
  WeddingCapabilities,
  WeddingCandidate,
  WeddingJob,
  WeddingProject,
  WeddingProjectDetail,
  WeddingRender,
  WeddingStoryboard,
  WeddingStoryDraft,
  WeddingStyle,
  WeddingCreationJob, WeddingPhotoReadiness, WeddingMusicTrack,
} from "./types";

const TOKEN_KEY = "aiwardrobe_token";
const IDEMPOTENCY_PREFIX = "aiwardrobe_idempotency_v1:";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code: string | null,
    readonly retryAfter: string | null
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export function getStoredToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function storeToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}

async function request<T>(path: string, options: RequestInit = {}, token?: string | null): Promise<T> {
  const headers = new Headers(options.headers);
  if (!(options.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }

  const response = await fetch(path, { ...options, headers });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = body && typeof body === "object" ? (body as { detail?: unknown }).detail : undefined;
    const errorRecord = detail && typeof detail === "object" ? detail as Record<string, unknown> : {};
    const code = typeof errorRecord.code === "string" ? errorRecord.code : null;
    throw new ApiError(errorDetail(detail), response.status, code, response.headers?.get?.("Retry-After") ?? null);
  }
  return body as T;
}

function errorDetail(detail: unknown): string {
  if (typeof detail === "string" && detail) return detail;
  if (detail && typeof detail === "object") {
    const record = detail as Record<string, unknown>;
    if (typeof record.message === "string") return record.message;
    if (typeof record.code === "string") return record.code;
    return JSON.stringify(detail);
  }
  return "请求失败，请稍后重试";
}

export function createIdempotencyKey(): string {
  return crypto.randomUUID();
}

export async function fingerprintFile(file: File): Promise<string> {
  const bytes = typeof file.arrayBuffer === "function" ? await file.arrayBuffer() : await readFileWithReader(file);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

export async function fingerprintText(value: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

function readFileWithReader(file: File): Promise<ArrayBuffer> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error || new Error("file_read_failed"));
    reader.onload = () => reader.result instanceof ArrayBuffer ? resolve(reader.result) : reject(new Error("file_read_failed"));
    reader.readAsArrayBuffer(file);
  });
}

export async function stableIdempotencyKey(token: string, operation: string, fingerprint: string): Promise<string> {
  const account = await tokenPartition(token);
  const storageKey = `${IDEMPOTENCY_PREFIX}${account}:${operation}:${fingerprint}`;
  try {
    const stored = localStorage.getItem(storageKey);
    if (stored) {
      const value = JSON.parse(stored) as { key?: string; createdAt?: number };
      if (value.key && value.createdAt && Date.now() - value.createdAt < 24 * 60 * 60 * 1000) return value.key;
    }
    const key = createIdempotencyKey();
    localStorage.setItem(storageKey, JSON.stringify({ key, createdAt: Date.now() }));
    return key;
  } catch {
    return createIdempotencyKey();
  }
}

export async function clearIdempotencyKey(token: string, operation: string, fingerprint: string): Promise<void> {
  const account = await tokenPartition(token);
  try { localStorage.removeItem(`${IDEMPOTENCY_PREFIX}${account}:${operation}:${fingerprint}`); } catch { /* storage is optional */ }
}

async function tokenPartition(token: string): Promise<string> {
  try {
    const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(token));
    return Array.from(new Uint8Array(digest).slice(0, 12), (byte) => byte.toString(16).padStart(2, "0")).join("");
  } catch {
    let hash = 2166136261;
    for (const character of token) hash = Math.imul(hash ^ character.charCodeAt(0), 16777619);
    return (hash >>> 0).toString(16);
  }
}

export function createGarmentUploadTask(token: string, file: File, mode: "single" | "split", key: string) {
  const form = new FormData();
  form.append("file", file);
  form.append("mode", mode);
  return request<AnalysisTask>("/v2/tasks/garment-upload", {
    method: "POST",
    headers: { "Idempotency-Key": key },
    body: form
  }, token);
}

export function createPurchaseUrlTask(token: string, url: string, key: string) {
  return request<AnalysisTask>("/v2/tasks/purchase-analysis", {
    method: "POST",
    headers: { "Idempotency-Key": key },
    body: JSON.stringify({ url })
  }, token);
}

export function createPurchaseImageTask(token: string, file: File, key: string, productUrl = "") {
  const form = new FormData();
  form.append("file", file);
  if (productUrl) form.append("product_url", productUrl);
  return request<AnalysisTask>("/v2/tasks/purchase-analysis", {
    method: "POST",
    headers: { "Idempotency-Key": key },
    body: form
  }, token);
}

export function fetchAnalysisTask(token: string, id: string, signal?: AbortSignal) {
  return request<AnalysisTask>(`/v2/tasks/${encodeURIComponent(id)}`, { signal }, token);
}

export function fetchAnalysisTasks(token: string, filters: { status?: string; cursor?: string; limit?: number; signal?: AbortSignal } = {}) {
  const params = new URLSearchParams();
  const { signal, ...query } = filters;
  Object.entries(query).forEach(([key, value]) => {
    if (value !== undefined && value !== "") params.set(key, String(value));
  });
  return request<{ items: AnalysisTask[]; next_cursor: string | null; has_more: boolean }>(
    `/v2/tasks${params.size ? `?${params}` : ""}`,
    { signal },
    token
  );
}

export async function waitForAnalysisTask(
  token: string,
  id: string,
  options: { signal?: AbortSignal; intervalMs?: number; onUpdate?: (task: AnalysisTask) => void } = {}
): Promise<AnalysisTask> {
  const interval = options.intervalMs ?? 1500;
  while (true) {
    if (options.signal?.aborted) throw new DOMException("Task polling cancelled", "AbortError");
    const task = await fetchAnalysisTask(token, id, options.signal);
    options.onUpdate?.(task);
    if (["succeeded", "failed", "cancelled"].includes(task.status) || (task.action_required && task.stage === "awaiting_resolution")) return task;
    const delay = Math.max(500, Math.min(30000, (task.poll_after_seconds ?? interval / 1000) * 1000));
    await wait(delay, options.signal);
  }
}

export function reconcileAnalysisTask(token: string, id: string) {
  return request<AnalysisTask>(`/v2/tasks/${encodeURIComponent(id)}/reconcile`, {
    method: "POST", body: JSON.stringify({})
  }, token);
}

function wait(delay: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(new DOMException("Task polling cancelled", "AbortError"));
    const timeout = window.setTimeout(() => {
      signal?.removeEventListener("abort", abort);
      resolve();
    }, delay);
    const abort = () => {
      window.clearTimeout(timeout);
      signal?.removeEventListener("abort", abort);
      reject(new DOMException("Task polling cancelled", "AbortError"));
    };
    signal?.addEventListener("abort", abort, { once: true });
  });
}

export function fetchV2Candidates(token: string, filters: { decision_status?: string; cursor?: string; limit?: number; signal?: AbortSignal } = {}) {
  const params = new URLSearchParams();
  const { signal, ...query } = filters;
  Object.entries(query).forEach(([key, value]) => {
    if (value !== undefined && value !== "") params.set(key, String(value));
  });
  return request<{ items: CandidateV2[]; next_cursor: string | null; has_more: boolean }>(`/v2/purchase/candidates${params.size ? `?${params}` : ""}`, { signal }, token);
}

export function fetchV2Candidate(token: string, id: string, signal?: AbortSignal) {
  return request<CandidateV2>(`/v2/purchase/candidates/${encodeURIComponent(id)}`, { signal }, token);
}

export function getV2Garment(token: string, id: string, signal?: AbortSignal) {
  return request<Garment>(`/v2/garments/${encodeURIComponent(id)}`, { signal }, token);
}

export function cancelAnalysisTask(token: string, id: string) {
  return request<AnalysisTask>(`/v2/tasks/${encodeURIComponent(id)}/cancel`, { method: "POST", body: JSON.stringify({}) }, token);
}

export function retryAnalysisTask(token: string, id: string, key: string) {
  return request<AnalysisTask>(`/v2/tasks/${encodeURIComponent(id)}/retry`, {
    method: "POST",
    headers: { "Idempotency-Key": key },
    body: JSON.stringify({})
  }, token);
}

export function saveV2Candidate(token: string, id: string, expectedRevision: number, key: string) {
  return request<{ garment_id: string; candidate_id: string; ownership_state: string; revision: number }>(
    `/v2/purchase/candidates/${encodeURIComponent(id)}/save`,
    { method: "POST", headers: { "Idempotency-Key": key }, body: JSON.stringify({ expected_revision: expectedRevision, confirm_owned: true }) },
    token
  );
}

export async function confirmV2Garment(token: string, id: string, tags: string[]) {
  await request(`/v2/garments/${encodeURIComponent(id)}/confirm`, {
    method: "POST", body: JSON.stringify({ tags })
  }, token);
  return getV2Garment(token, id);
}

export function reanalyzeV2Candidate(token: string, id: string, key: string) {
  return request<AnalysisTask>(`/v2/purchase/candidates/${encodeURIComponent(id)}/reanalyze`, {
    method: "POST", headers: { "Idempotency-Key": key }
  }, token);
}

export function createV2Feedback(token: string, body: {
  target_type: "candidate" | "outfit";
  target_id: string;
  snapshot_revision: number;
  value: "helpful" | "not_helpful";
  reason_code?: string;
  garment_ids?: string[];
  occasion?: string;
  exclude_next_time?: boolean;
}, key: string) {
  return request<DecisionFeedback>("/v2/feedback", {
    method: "POST", headers: { "Idempotency-Key": key }, body: JSON.stringify(body)
  }, token);
}

export function fetchV2Feedback(token: string, filters: { cursor?: string; limit?: number; signal?: AbortSignal } = {}) {
  const params = new URLSearchParams();
  const { signal, ...query } = filters;
  Object.entries(query).forEach(([key, value]) => {
    if (value !== undefined && value !== "") params.set(key, String(value));
  });
  return request<{ items: DecisionFeedback[]; next_cursor: string | null; has_more: boolean }>(`/v2/feedback${params.size ? `?${params}` : ""}`, { signal }, token);
}

export function revokeV2Feedback(token: string, id: string) {
  return request<void>(`/v2/feedback/${encodeURIComponent(id)}`, { method: "DELETE" }, token);
}

export function createV2WearEvent(token: string, body: {
  local_date: string;
  timezone: string;
  outfit_id?: string;
  garment_ids?: string[];
  occasion?: string;
}, key: string) {
  return request<WearEvent>("/v2/wear-events", {
    method: "POST", headers: { "Idempotency-Key": key }, body: JSON.stringify(body)
  }, token);
}

export function fetchV2WearEvents(token: string, filters: { start?: string; end?: string; cursor?: string; limit?: number; signal?: AbortSignal } = {}) {
  const params = new URLSearchParams();
  const { signal, ...query } = filters;
  Object.entries(query).forEach(([key, value]) => {
    if (value !== undefined && value !== "") params.set(key, String(value));
  });
  return request<{ items: WearEvent[]; next_cursor: string | null; has_more: boolean }>(`/v2/wear-events${params.size ? `?${params}` : ""}`, { signal }, token);
}

export function revokeV2WearEvent(token: string, id: string) {
  return request<void>(`/v2/wear-events/${encodeURIComponent(id)}`, { method: "DELETE" }, token);
}

export function updateV2Candidate(token: string, id: string, body: Record<string, unknown>) {
  return request<CandidateV2>(`/v2/purchase/candidates/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify(body) }, token);
}

export function decideV2Candidate(token: string, id: string, decisionStatus: string, expectedRevision: number) {
  return request<CandidateV2>(`/v2/purchase/candidates/${encodeURIComponent(id)}/decision`, {
    method: "PATCH",
    body: JSON.stringify({ decision_status: decisionStatus, expected_revision: expectedRevision })
  }, token);
}

export function fetchV2Garments(token: string, filters: { category?: string; tag?: string; color?: string; season?: string; q?: string; cursor?: string; limit?: number; status?: string; signal?: AbortSignal } = {}) {
  const params = new URLSearchParams();
  const { signal, ...query } = filters;
  Object.entries(query).forEach(([key, value]) => {
    if (value !== undefined && value !== "") params.set(key, String(value));
  });
  return request<{ items: Garment[]; next_cursor: string | null; has_more: boolean }>(`/v2/garments${params.size ? `?${params}` : ""}`, { signal }, token);
}

export function fetchV2Outfits(token: string, filters: { favorite?: boolean; fixed?: boolean; occasion?: string; cursor?: string; limit?: number; signal?: AbortSignal } = {}) {
  const params = new URLSearchParams();
  const { signal, ...query } = filters;
  Object.entries(query).forEach(([key, value]) => {
    if (value !== undefined && value !== "") params.set(key, String(value));
  });
  return request<{ items: Outfit[]; next_cursor: string | null; has_more: boolean }>(`/v2/outfits${params.size ? `?${params}` : ""}`, { signal }, token);
}

export function linkV2Candidate(token: string, candidateId: string, garmentId: string, expectedRevision: number, key = createIdempotencyKey()) {
  return request<Record<string, unknown>>(`/v2/purchase/candidates/${encodeURIComponent(candidateId)}/link-owned-garment`, {
    method: "POST",
    headers: { "Idempotency-Key": key },
    body: JSON.stringify({ expected_revision: expectedRevision, garment_id: garmentId, confirm_owned: true })
  }, token);
}

export function requestEmailCode(email: string) {
  return request<{ message: string; dev_code?: string }>("/auth/email-code/request", {
    method: "POST",
    body: JSON.stringify({ email })
  });
}

export function verifyEmailCode(email: string, code: string) {
  return request<AuthResponse>("/auth/email-code/verify", {
    method: "POST",
    body: JSON.stringify({ email, code })
  });
}

export function registerWithPassword(email: string, password: string) {
  return request<AuthResponse>("/auth/register", {
    method: "POST",
    body: JSON.stringify({ email, password })
  });
}

export function loginWithPassword(email: string, password: string) {
  return request<AuthResponse>("/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password })
  });
}

export function fetchUserPreference(token: string) {
  return request<UserPreference>("/preferences/me", {}, token);
}

export function updateUserPreference(token: string, body: UserPreference) {
  return request<UserPreference>("/preferences/me", { method: "PUT", body: JSON.stringify(body) }, token);
}

export function fetchGarments(token: string, filters: { category?: string; tag?: string; color?: string; season?: string } = {}) {
  const params = new URLSearchParams();
  Object.entries(filters).forEach(([key, value]) => {
    if (value) params.set(key, value);
  });
  const query = params.toString();
  return request<{ items: Garment[] }>(`/garments${query ? `?${query}` : ""}`, {}, token);
}

export function uploadGarment(token: string, file: File) {
  const form = new FormData();
  form.append("file", file);
  return request<Garment>("/garments/upload", { method: "POST", body: form }, token);
}

export function uploadGarmentPhoto(token: string, file: File) {
  const form = new FormData();
  form.append("file", file);
  return request<UploadSession>("/uploads/garment-photo", { method: "POST", body: form }, token);
}

export function uploadPlainGarment(token: string, file: File) {
  const form = new FormData();
  form.append("file", file);
  return request<Garment>("/uploads/plain-garment", { method: "POST", body: form }, token);
}

export function batchUploadGarments(token: string, files: File[]) {
  const form = new FormData();
  files.forEach((file) => form.append("files", file));
  return request<{ items: Garment[] }>("/garments/batch-upload", { method: "POST", body: form }, token);
}

export function analyzePurchaseUrl(token: string, url: string) {
  return request<PurchaseCandidate>("/purchase/analyze", { method: "POST", body: JSON.stringify({ url }) }, token);
}

export function analyzePurchaseImage(token: string, file: File, productUrl = "") {
  const form = new FormData();
  form.append("file", file);
  if (productUrl) form.append("product_url", productUrl);
  return request<PurchaseCandidate>("/purchase/analyze-image", { method: "POST", body: form }, token);
}

export function savePurchaseCandidate(token: string, id: string) {
  return request<Garment>(`/purchase/candidates/${id}/save`, { method: "POST" }, token);
}

export function createShoppingRecommendations(
  token: string,
  body: { target: ShoppingRecommendationTarget; refresh: boolean }
) {
  return request<ShoppingRecommendationRun>("/shopping/recommendations", {
    method: "POST",
    body: JSON.stringify(body)
  }, token);
}

export function analyzeShoppingRecommendationItem(token: string, id: string) {
  return request<ShoppingRecommendationItem>(`/shopping/recommendations/items/${id}/analyze`, { method: "POST" }, token);
}

export function updateGarment(token: string, id: string, body: Partial<Garment>) {
  return request<Garment>(`/garments/${id}`, { method: "PATCH", body: JSON.stringify(body) }, token);
}

export function deleteGarment(token: string, id: string) {
  return request<void>(`/garments/${id}`, { method: "DELETE" }, token);
}

export function fetchTodayWeather(token: string, lat: number, lon: number) {
  return request<Weather>(`/weather/today?lat=${encodeURIComponent(lat)}&lon=${encodeURIComponent(lon)}`, {}, token);
}

export function generateOutfit(token: string, body: {
  occasion: Occasion;
  season?: string;
  temperature: number;
  weather?: Weather | null;
  garment_id?: string | null;
  purchase_candidate_id?: string | null;
}) {
  return request<Outfit>("/outfits/generate", { method: "POST", body: JSON.stringify(body) }, token);
}

export function createManualOutfit(token: string, body: {
  name: string;
  garment_ids: string[];
  occasion: Occasion;
  season?: string;
  temperature?: number | null;
  is_fixed: boolean;
  weather?: Weather | null;
}) {
  return request<Outfit>("/outfits/manual", { method: "POST", body: JSON.stringify(body) }, token);
}

export function fetchOutfits(token: string, favorite?: boolean) {
  return request<{ items: Outfit[] }>(`/outfits/history${favorite === undefined ? "" : `?favorite=${favorite}`}`, {}, token);
}

export function fetchWardrobeReport(token: string) {
  return request<WardrobeReport>("/reports/wardrobe", {}, token);
}

export function deleteOutfit(token: string, id: string) {
  return request<void>(`/outfits/${id}`, { method: "DELETE" }, token);
}

export function setOutfitFavorite(token: string, id: string, isFavorite: boolean) {
  return request<Outfit>(`/outfits/${id}/favorite`, {
    method: "PATCH",
    body: JSON.stringify({ is_favorite: isFavorite })
  }, token);
}

export function setOutfitFixed(token: string, id: string, isFixed: boolean) {
  return request<Outfit>(`/outfits/${id}/fixed`, {
    method: "PATCH",
    body: JSON.stringify({ is_fixed: isFixed })
  }, token);
}

export function getWeddingCapabilities(token: string) {
  return request<WeddingCapabilities>("/wedding-capabilities", {}, token);
}

export function listWeddingProjects(token: string) {
  return request<WeddingProject[]>("/wedding-projects", {}, token);
}

export function createWeddingProject(token: string, body: { title: string; owner_name: string; style: WeddingStyle }) {
  return request<WeddingProject>("/wedding-projects", { method: "POST", body: JSON.stringify(body) }, token);
}

export function fetchWeddingProject(token: string, projectId: string) {
  return request<WeddingProjectDetail>(`/wedding-projects/${encodeURIComponent(projectId)}`, {}, token);
}

export function confirmWeddingOwnerConsent(token: string, projectId: string, providerList: string[] = ["local_demo"]) {
  return request(`/wedding-projects/${encodeURIComponent(projectId)}/self-consent`, {
    method: "POST",
    body: JSON.stringify({
      terms_version: "portrait-v1",
      scope: ["portrait_processing", "preview_review", "storage"],
      provider_list: providerList
    })
  }, token);
}

export function revokeWeddingConsent(token: string, projectId: string) {
  return request<{ revoked: boolean; person_key: "A" | "B"; revoked_at: string }>(
    `/wedding-projects/${encodeURIComponent(projectId)}/consent/revoke`,
    { method: "POST" },
    token
  );
}

export function deleteWeddingProject(token: string, projectId: string) {
  return request<void>(`/wedding-projects/${encodeURIComponent(projectId)}`, { method: "DELETE" }, token);
}

export function createWeddingInvite(token: string, projectId: string, displayName: string, providerList: string[]) {
  return request<{ id: string; invite_token: string; person_key: "B"; expires_at: string }>(
    `/wedding-projects/${encodeURIComponent(projectId)}/consent-invites`,
    { method: "POST", body: JSON.stringify({ person_key: "B", display_name: displayName, provider_list: providerList, terms_version: "portrait-v1" }) },
    token
  );
}

export type WeddingInviteDisclosure = { project_id: string; person_key: "B"; display_name: string; kind: "consent" | "access"; terms_version: string; provider_list: string[]; scope: string[]; purposes: string[]; deletion: string; expires_at: string; disclosure_hash: string };
export function previewWeddingInvite(token: string) {
  return request<WeddingInviteDisclosure>("/consent-invites/preview", {method:"POST",body:JSON.stringify({token})});
}
export function createWeddingAccessInvite(token: string, projectId: string) {
  return request<{invite_token:string}>(`/wedding-projects/${encodeURIComponent(projectId)}/access-invites`,{method:"POST"},token);
}
export function reviseWeddingStoryboard(token: string, projectId: string, draft: WeddingStoryDraft, fingerprint: string) {
  return request<WeddingStoryboard>(`/wedding-projects/${encodeURIComponent(projectId)}/storyboard/revisions`,
    {method:"POST",headers:{"If-Match":fingerprint},body:JSON.stringify(draft)},token);
}
export function acceptWeddingInvite(inviteToken: string, displayName: string, disclosure: WeddingInviteDisclosure) {
  return request<{
    access_token: string | null;
    accepted: boolean;
    project_id: string;
    participant: { person_key: "B"; display_name: string; consent_status: string; confirmed_at: string | null };
  }>("/consent-invites/confirm", {
    method: "POST",
    body: JSON.stringify({ token: inviteToken, accepted: true, display_name: displayName, terms_version: disclosure.terms_version, disclosure_hash: disclosure.disclosure_hash })
  });
}

export function uploadWeddingPhoto(token: string, projectId: string, file: File, personKey: "A" | "B") {
  const form = new FormData();
  form.append("file", file);
  form.append("person_key", personKey);
  form.append("asset_type", "photo");
  return request<WeddingAsset>(`/wedding-projects/${encodeURIComponent(projectId)}/assets`, { method: "POST", body: form }, token);
}

export function listWeddingAssets(token: string, projectId: string) {
  return request<WeddingAsset[]>(`/wedding-projects/${encodeURIComponent(projectId)}/assets`, {}, token);
}

async function requestWeddingBlob(path: string, token: string): Promise<Blob> {
  const response = await fetch(path, { headers: { Authorization: `Bearer ${token}` } });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(errorDetail(body.detail));
  }
  return response.blob();
}

export function fetchWeddingAssetBlob(token: string, assetId: string) {
  return requestWeddingBlob(`/wedding-assets/${encodeURIComponent(assetId)}`, token);
}

export function fetchWeddingStoryboard(token: string, projectId: string) {
  return request<WeddingStoryboard>(`/wedding-projects/${encodeURIComponent(projectId)}/storyboard`, {}, token);
}

export function saveWeddingStoryboard(token: string, projectId: string, body: WeddingStoryDraft, update: boolean, fingerprint?: string) {
  return request<WeddingStoryboard>(`/wedding-projects/${encodeURIComponent(projectId)}/storyboard`, {
    method: update ? "PUT" : "POST",
    headers: fingerprint ? {"If-Match": fingerprint} : {},
    body: JSON.stringify(body)
  }, token);
}

export function approveWeddingStoryboard(token: string, projectId: string, version: number, fingerprint?: string) {
  return request<WeddingStoryboard>(`/wedding-projects/${encodeURIComponent(projectId)}/storyboard/approve`, {
    method: "POST", headers: fingerprint ? {"If-Match": fingerprint} : {}, body: JSON.stringify({ version })
  }, token);
}

export function approveWeddingFrames(token: string, projectId: string, version: number, frames: Array<{ shot_id: string; asset_id: string }>) {
  return request<WeddingStoryboard>(`/wedding-projects/${encodeURIComponent(projectId)}/frames/approve`, {
    method: "POST", body: JSON.stringify({ version, frames })
  }, token);
}

export function submitWeddingShotReview(token: string, shotId: string, assetId: string, version: number, result: "approved" | "rejected") {
  return request(`/participant-reviews/${encodeURIComponent(shotId)}`, {
    method: "POST", body: JSON.stringify({ asset_id: assetId, project_version: version, result })
  }, token);
}

export function fetchWeddingJobs(token: string, projectId: string) {
  return request<WeddingJob[]>(`/wedding-projects/${encodeURIComponent(projectId)}/generation-jobs`, {}, token);
}

export function fetchWeddingCandidates(token: string, projectId: string) {
  return request<WeddingCandidate[]>(`/wedding-projects/${encodeURIComponent(projectId)}/candidates`, {}, token);
}

export function fetchWeddingRenders(token: string, projectId: string) {
  return request<WeddingRender[]>(`/wedding-projects/${encodeURIComponent(projectId)}/renders`, {}, token);
}

export function selectWeddingCandidate(token: string, projectId: string, shotId: string, candidateId: string, version: number) {
  return request<WeddingStoryboard>(`/wedding-projects/${encodeURIComponent(projectId)}/shots/${encodeURIComponent(shotId)}/select-candidate`, {
    method: "POST", body: JSON.stringify({ project_version: version, candidate_id: candidateId })
  }, token);
}

export function acceptWeddingPhotoFallback(token: string, projectId: string, shotId: string, assetId: string, version: number) {
  return request<WeddingStoryboard>(`/wedding-projects/${encodeURIComponent(projectId)}/shots/${encodeURIComponent(shotId)}/accept-fallback`, {
    method: "POST", body: JSON.stringify({ project_version: version, asset_id: assetId })
  }, token);
}

export function createWeddingQuote(token: string, projectId: string, shotId?: string) {
  return request<{ id: string; project_version: number; max_budget_fen: number; currency: "CNY"; price_version: string; issued_at: string; expires_at: string }>(
    `/wedding-projects/${encodeURIComponent(projectId)}/quote${shotId ? `?${new URLSearchParams({shot_id: shotId})}` : ""}`, {}, token
  );
}

export function createWeddingGenerationJob(token: string, projectId: string, quoteId: string, version: number) {
  return request<WeddingJob>(`/wedding-projects/${encodeURIComponent(projectId)}/generation-jobs`, {
    method: "POST",
    headers: { "Idempotency-Key": `wedding-${projectId}-${version}-${crypto.randomUUID()}` },
    body: JSON.stringify({ quote_id: quoteId, version })
  }, token);
}

export function regenerateWeddingShot(token: string, projectId: string, shotId: string, quoteId: string, version: number) {
  const requestId = typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  return request<WeddingJob>(`/wedding-projects/${encodeURIComponent(projectId)}/shots/${encodeURIComponent(shotId)}/regenerate`, {
    method: "POST",
    headers: { "Idempotency-Key": `wedding-shot-${projectId}-${shotId}-${version}-${requestId}` },
    body: JSON.stringify({ quote_id: quoteId, version })
  }, token);
}

export function createWeddingRender(token: string, projectId: string, version: number, edits: {subtitles?: Record<string,string>; music_track_id?: string; source_render_id?: string} = {}) {
  return request<WeddingRender>(`/wedding-projects/${encodeURIComponent(projectId)}/render`, {
    method: "POST", body: JSON.stringify({ version, ...edits })
  }, token);
}

export function submitWeddingRenderReview(token: string, projectId: string, renderId: string, assetId: string, version: number) {
  const params = new URLSearchParams({ render_version_id: renderId });
  return request(`/wedding-projects/${encodeURIComponent(projectId)}/render-reviews?${params}`, {
    method: "POST", body: JSON.stringify({ asset_id: assetId, project_version: version, result: "approved" })
  }, token);
}

export function approveWeddingRender(token: string, projectId: string, renderId: string) {
  const params = new URLSearchParams({ render_version_id: renderId });
  return request<WeddingRender>(`/wedding-projects/${encodeURIComponent(projectId)}/approve?${params}`, { method: "POST" }, token);
}

export function exportWeddingRender(token: string, projectId: string, renderId: string) {
  return request<{ render_version_id: string; asset_id: string; mime: string; size_bytes: number }>(
    `/wedding-projects/${encodeURIComponent(projectId)}/export?${new URLSearchParams({render_version_id: renderId})}`, { method: "POST" }, token
  );
}

const weddingPath = (id: string) => `/wedding-projects/${encodeURIComponent(id)}`;
export function fetchWeddingCreationJobs(token: string, id: string) {
  return request<WeddingCreationJob[]>(`${weddingPath(id)}/creation-jobs`, {}, token);
}
export function createWeddingStoryJob(token: string, id: string, version: number, story_text: string) {
  return request<WeddingCreationJob>(`${weddingPath(id)}/story-jobs`, {method:"POST", headers:{"Idempotency-Key": crypto.randomUUID()}, body:JSON.stringify({version,story_text})}, token);
}
export function createWeddingFrameJob(token: string, id: string, version: number, photos: Array<{shot_id:string;asset_id:string}>) {
  return request<WeddingCreationJob>(`${weddingPath(id)}/frame-jobs`, {method:"POST", headers:{"Idempotency-Key":crypto.randomUUID()}, body:JSON.stringify({version,photos})}, token);
}
export function cancelWeddingCreationJob(token:string,id:string,jobId:string) {
  return request<WeddingCreationJob>(`${weddingPath(id)}/creation-jobs/${encodeURIComponent(jobId)}/cancel`, {method:"POST"}, token);
}
export function fetchWeddingPhotoReadiness(token: string,id: string) {
  return request<WeddingPhotoReadiness>(`${weddingPath(id)}/photo-readiness`, {}, token);
}
export function analyzeWeddingPhoto(token:string,id:string,assetId:string) {
  return request(`${weddingPath(id)}/photos/${encodeURIComponent(assetId)}/analyze-quality`, {method:"POST"}, token);
}
export function confirmWeddingPhotoQuality(token:string,id:string,assetId:string,confirmed:boolean) {
  return request(`${weddingPath(id)}/photos/${encodeURIComponent(assetId)}/confirm-quality`, {method:"POST",body:JSON.stringify({confirmed})}, token);
}
export function selectWeddingPrimaryPhoto(token:string,id:string,person:"A"|"B",version:number,asset_id:string) {
  return request<{cost_notice:string; affected_shot_ids:string[]; regeneration_required:boolean}>(`${weddingPath(id)}/participants/${person}/primary-photo`, {method:"PUT",body:JSON.stringify({version,asset_id})}, token);
}
export function fetchWeddingMusic(token:string) {
  return request<{available:boolean; tracks:WeddingMusicTrack[]}>("/wedding-music",{},token);
}
export function selectWeddingMusic(token:string,id:string,track_id:string) {
  return request<WeddingMusicTrack>(`${weddingPath(id)}/music`, {method:"PUT",body:JSON.stringify({track_id})},token);
}
export function restoreWeddingRender(token:string,id:string,renderId:string,version:number) {
  return request<WeddingRender>(`${weddingPath(id)}/renders/${encodeURIComponent(renderId)}/restore`, {method:"POST",body:JSON.stringify({version})}, token);
}
