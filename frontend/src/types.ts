export type Category = "top" | "bottom" | "outerwear" | "dress" | "shoes" | "bag" | "accessory";
export type GarmentStatus = "uploaded" | "extracting" | "tagging" | "pending_review" | "processing" | "ready" | "failed";
export type Occasion = "work" | "date" | "sport" | "formal" | "casual";
export type PurchaseRecommendation = "recommend" | "consider" | "skip";
export type ShoppingRecommendationTarget = "auto_gap" | "work" | "date" | "sport" | "summer" | "basics";
export type ShoppingAnalysisStatus = "pending_analysis" | "analyzing" | "analyzed" | "failed";

export type User = {
  id: string;
  email: string;
  created_at: string;
  updated_at: string;
};

export type UserPreference = {
  primary_goal: string;
  scenes: string[];
  styles: string[];
  avoid_types: string[];
  budget_range: string;
  budget_min?: string | null;
  budget_max?: string | null;
  currency?: "CNY";
  avoid_categories?: Category[];
};

export type Garment = {
  id: string;
  source_upload_id: string | null;
  image_url: string;
  image_key: string;
  thumbnail_url: string | null;
  category: Category;
  colors: string[];
  style: string;
  material: string;
  season: string[];
  fit: string;
  tags: string[];
  crop_box: { x: number; y: number; width: number; height: number } | null;
  ai_result: Record<string, unknown>;
  ai_confidence: number;
  status: GarmentStatus;
  review_status: "pending_review" | "confirmed" | string;
  user_confirmed_at?: string | null;
  created_at: string;
  updated_at: string;
};

export type UploadSession = {
  id: string;
  original_image_url: string;
  original_image_key: string;
  status: string;
  error_message: string | null;
  garments: Garment[];
  created_at: string;
  updated_at: string;
};

export type Weather = {
  date: string;
  lat_key: string;
  lon_key: string;
  city: string;
  condition: string;
  temperature: number;
  feels_like: number;
  precipitation: number;
  wind_speed: number;
  cached: boolean;
};

export type Outfit = {
  id: string;
  name: string;
  occasion: Occasion;
  season: string;
  temperature: number | null;
  items: Array<{
    garment_id: string;
    category: string;
    image_url: string;
    reason: string;
  }>;
  explanation: string;
  source: "ai" | "manual" | string;
  is_favorite: boolean;
  is_fixed: boolean;
  weather_snapshot: Record<string, unknown> | null;
  result_revision?: number;
  created_at: string;
};

export type PurchaseCandidate = {
  id: string;
  product_url: string;
  source_image_url: string;
  image_url: string;
  image_key: string;
  thumbnail_url: string | null;
  title: string;
  domain: string;
  category: Category;
  colors: string[];
  style: string;
  material: string;
  season: string[];
  fit: string;
  tags: string[];
  ai_result: Record<string, unknown>;
  ai_confidence: number;
  similar_items: Array<{
    garment_id: string;
    image_url: string;
    similarity: number;
    matched_reasons: string[];
  }>;
  recommendation: PurchaseRecommendation;
  score: number | null;
  reason_summary: string;
  analysis: {
    conclusion: PurchaseRecommendation;
    score: number | null;
    summary: string;
    dimensions: {
      outfit_potential: number | null;
      scene_match: number | null;
      gap_fill: number | null;
      duplicate_risk: number | null;
      idle_risk: number | null;
      budget_match?: boolean | null;
    };
    duplicate_risk: number | null;
    idle_risk: number | null;
    outfit_potential: number | null;
    match_scenes: string[];
    suggested_price: { min: number; ideal: number; max: number } | null;
    product_price?: { current: string; currency: string; source: string } | null;
    score_breakdown: {
      duplicate_risk: number | null;
      wardrobe_gap: number | null;
      outfit_potential: number | null;
      scene_match: number | null;
      idle_risk: number | null;
    } | null;
    pros: string[];
    cons: string[];
    outfit_ideas: Array<{
      scene: string;
      items: Array<{ category: string; image_url?: string; asset_id?: string; garment_id?: string; candidate_id?: string; reason: string }>;
      seasons?: string[];
      warnings?: string[];
      reason: string;
    }>;
    idle_risk_detail: { level: string; reason: string } | null;
    next_actions: string[];
    duplicate_score: number | null;
    wardrobe_gap_score: number | null;
    pairing_score: number | null;
    decision_factors: string[];
    similar_items: Array<{
      garment_id: string;
      image_url: string;
      similarity: number;
      matched_reasons: string[];
    }>;
  };
  status: "analyzing" | "ready" | "failed" | "saved";
  created_at: string;
  updated_at: string;
};

export type ShoppingRecommendationItem = {
  id: string;
  platform: string;
  platform_item_id: string;
  title: string;
  image_url: string;
  price: string;
  shop_name: string;
  product_url: string;
  analysis_status: ShoppingAnalysisStatus;
  purchase_candidate_id: string | null;
  recommendation: PurchaseRecommendation | null;
  score: number | null;
  reason_summary: string;
  similar_items: Array<{
    garment_id: string;
    image_url: string;
    similarity: number;
    matched_reasons: string[];
  }>;
};

export type ShoppingRecommendationRun = {
  id: string;
  target: ShoppingRecommendationTarget;
  keywords: string[];
  status: "running" | "ready" | "failed" | "rate_limited";
  error_code: string | null;
  cache_hit: boolean;
  rate_limit: {
    remaining_refreshes: number | null;
    reset_at: string | null;
  };
  wardrobe_gaps: Array<{ category: Category; label: string; score: number; reason: string }>;
  avoid_categories: string[];
  recommendation_groups: Array<{ title: string; reason: string; item_ids: string[] }>;
  items: ShoppingRecommendationItem[];
  created_at: string;
  updated_at: string;
};

export type WardrobeReport = {
  total: number;
  ready_total: number;
  summary: string;
  category_distribution: Array<{ key: string; label: string; count: number; ratio: number }>;
  color_distribution: Array<{ key: string; label: string; count: number; ratio: number }>;
  style_distribution: Array<{ key: string; label: string; count: number; ratio: number }>;
  scene_coverage: Record<string, number>;
  duplicate_risks: Array<{ category: Category; label: string; colors: string[]; count: number; garment_ids: string[] }>;
  low_use_items: Array<{ garment_id: string; category: Category; label: string; reason: string; score: number }>;
  wardrobe_gaps: Array<{ category: Category; label: string; score: number; reason: string }>;
  avoid_categories: string[];
  suggested_categories: string[];
};

export type AuthResponse = {
  access_token: string;
  token_type: string;
  user: User;
};

export type AnalysisTask = {
  id: string;
  kind: "garment_single" | "garment_split" | "purchase_url" | "purchase_image" | "recommendation_item" | "purchase_reanalysis";
  status: "queued" | "running" | "succeeded" | "failed" | "cancelled";
  stage: "validating" | "downloading" | "preprocessing" | "recognizing" | "scoring" | "persisting" | "awaiting_resolution";
  submission_status: "not_submitted" | "submitting" | "submitted" | "unknown" | "completed" | "failed";
  cancel_requested: boolean;
  action_required: boolean;
  attempt_count: number;
  stage_durations_ms?: Record<string, number>;
  operations?: Array<{
    operation_key: string;
    generation: number;
    provider: string;
    submission_status: "not_submitted" | "submitting" | "submitted" | "unknown" | "completed" | "failed";
    error_code: string | null;
    duration_ms: number;
    usage: Record<string, number> | null;
    reported_cost: { amount: string; currency: string } | null;
  }>;
  error: { code: string; retryable: boolean } | null;
  result_refs: Array<{ resource_type: string; id: string }>;
  poll_after_seconds: number | null;
  created_at: string;
  updated_at: string;
};

export type DecisionFeedback = {
  id: string;
  target_type: "candidate" | "outfit";
  target_id: string;
  snapshot_revision: number;
  value: "helpful" | "not_helpful";
  reason_code: string | null;
  garment_ids: string[];
  context: Record<string, unknown>;
  revoked_at: string | null;
  created_at: string;
};

export type WearEvent = {
  id: string;
  local_date: string;
  timezone: string;
  outfit_id: string | null;
  occasion: string | null;
  items: Array<{ garment_id: string; snapshot: Record<string, unknown> }>;
  revoked_at: string | null;
  created_at: string;
};

export type CandidateV2 = {
  id: string;
  title: string;
  product_url: string;
  domain: string;
  asset_id: string | null;
  image_url: string | null;
  similar_items: Array<{ garment_id: string; image_url: string; similarity: number; matched_reasons: string[] }>;
  category: Category;
  colors: string[];
  style: string;
  material: string;
  season: string[];
  fit: string;
  tags: string[];
  analysis_source: "remote" | "manual" | "demo" | "legacy_unknown";
  actual_price: string | null;
  currency: string | null;
  recommendation: PurchaseRecommendation;
  score: number | null;
  reason_summary: string;
  analysis: Partial<PurchaseCandidate["analysis"]>;
  coverage: Record<string, unknown>;
  evidence: Record<string, unknown>;
  decision_status: "considering" | "skipped" | "bought" | "returned";
  analysis_status: string;
  ownership_state: "never_saved" | "linked" | "removed" | "legacy_unlinked" | string;
  owned_garment_id: string | null;
  revision: number;
  snapshot_revision: number | null;
  stale: boolean;
  created_at: string;
  updated_at: string;
  snapshots?: Array<Record<string, unknown>>;
};
