import type { AuthResponse, Garment, Occasion, Outfit, UploadSession, Weather } from "./types";

const TOKEN_KEY = "aiwardrobe_token";

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
    throw new Error(body.detail || "请求失败，请稍后重试");
  }
  return body as T;
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

export function updateGarment(token: string, id: string, body: Partial<Garment>) {
  return request<Garment>(`/garments/${id}`, { method: "PATCH", body: JSON.stringify(body) }, token);
}

export function deleteGarment(token: string, id: string) {
  return request<void>(`/garments/${id}`, { method: "DELETE" }, token);
}

export function fetchTodayWeather(token: string, lat: number, lon: number) {
  return request<Weather>(`/weather/today?lat=${encodeURIComponent(lat)}&lon=${encodeURIComponent(lon)}`, {}, token);
}

export function generateOutfit(token: string, body: { occasion: Occasion; season: string; temperature: number; weather?: Weather | null }) {
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
