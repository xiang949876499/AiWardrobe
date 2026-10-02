export type Category = "top" | "bottom" | "outerwear" | "shoes" | "bag" | "accessory";
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
  score: number;
  reason_summary: string;
  analysis: {
    conclusion: PurchaseRecommendation;
    score: number;
    summary: string;
    dimensions: {
      outfit_potential: number;
      scene_match: number;
      gap_fill: number;
      duplicate_risk: number;
      idle_risk: number;
    };
    duplicate_risk: number;
    idle_risk: number;
    outfit_potential: number;
    match_scenes: string[];
    suggested_price: { min: number; ideal: number; max: number };
    product_price?: { current: string; currency: string; source: string } | null;
    score_breakdown: {
      duplicate_risk: number;
      wardrobe_gap: number;
      outfit_potential: number;
      scene_match: number;
      idle_risk: number;
    };
    pros: string[];
    cons: string[];
    outfit_ideas: Array<{
      scene: string;
      items: Array<{ category: string; image_url: string; reason: string }>;
      reason: string;
    }>;
    idle_risk_detail: { level: "低" | "中" | "高"; reason: string };
    next_actions: string[];
    duplicate_score: number;
    wardrobe_gap_score: number;
    pairing_score: number;
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

export type WeddingStyle = "warm_documentary" | "romantic_film" | "fresh_illustration";
export type WeddingPersonKey = "A" | "B";

export type WeddingProject = {
  id: string;
  title: string;
  status: string;
  version: number;
  style: WeddingStyle;
  target_duration_ms: number;
  aspect_ratio: string;
  budget_limit_fen: number;
  budget_reserved_fen: number;
  budget_spent_fen: number;
  final_download_count?: number;
  final_download_limit?: number;
  preview_bytes_served?: number;
  preview_bandwidth_limit_bytes?: number;
  retention_expires_at?: string;
  created_at: string;
  updated_at: string;
};

export type WeddingParticipant = {
  primary_photo_asset_id?: string | null;
  person_key: WeddingPersonKey;
  display_name: string;
  consent_status: "pending" | "active" | "revoked" | "declined";
  confirmed_at: string | null;
};

export type WeddingProjectDetail = WeddingProject & {
  participants: WeddingParticipant[];
  story_text: string | null;
};

export type WeddingAsset = {
  expires_at?: string;
  quality?: WeddingPhotoQuality;
  quality_confirmed_at?: string | null;
  id: string;
  asset_type: string;
  person_key: WeddingPersonKey | null;
  mime: string;
  size_bytes: number;
  width: number | null;
  height: number | null;
  generated: boolean;
  created_at: string;
};

export type WeddingIdentityReview = {
  person_key: WeddingPersonKey;
  asset_id: string;
  result: "approved" | "rejected";
  confirmed_at: string;
};

export type WeddingFact = {
  id: string;
  source_field: string;
  text: string;
  confirmed_at: string | null;
};

export type WeddingShot = {
  id: string;
  source_shot_id?: string | null;
  generation_attempts?: number;
  project_version: number;
  shot_index: number;
  shot_type: "person_ai" | "scene_ai" | "photo";
  duration_ms: number;
  person_keys: WeddingPersonKey[];
  fact_ids: string[];
  description: string;
  subtitle: string;
  creative_scene: boolean;
  frame_asset_id: string | null;
  selected_asset_id: string | null;
  selected_candidate_id: string | null;
  status: string;
  identity_reviews: WeddingIdentityReview[];
};

export type WeddingStoryboard = {
  draft_fingerprint?: string | null;
  id: string;
  version: number;
  story_text: string;
  style: WeddingStyle;
  status: string;
  owner_approved_at: string | null;
  facts: WeddingFact[];
  shots: WeddingShot[];
};

export type WeddingJob = {
  id: string;
  project_id: string;
  project_version: number;
  status: "queued" | "processing" | "needs_action" | "completed" | "failed" | "cancel_requested" | "cancelled";
  progress_stage: string;
  completed_shots: number;
  total_shots: number;
  reserved_amount_fen: number;
  actual_cost_fen: number;
  error_code: string | null;
  created_at: string;
  updated_at: string;
};

export type WeddingCandidate = {
  id: string;
  shot_id: string;
  generation_job_id: string;
  asset_id: string;
  asset: WeddingAsset;
  provider: string;
  model_version: string;
  quality_flags: string[];
  review_result: "pending" | "approved" | "rejected";
  actual_cost_fen: number;
  created_at: string;
};

export type WeddingRender = {
  timeline?: Array<{shot_id: string; shot_index: number; subtitle: string; duration_ms: number; asset_id: string}>;
  music?: WeddingMusicTrack | null;
  restored_from_id?: string | null;
  invalidated_at?: string | null;
  attempts?: number;
  started_at?: string | null;
  updated_at?: string;
  error_code?: string | null;
  id: string;
  project_version: number;
  version: number;
  status: "queued" | "processing" | "ready" | "approved" | "failed" | "cancelled";
  preview_asset_id: string | null;
  export_asset_id: string | null;
  output_duration_ms: number | null;
  owner_approved_at: string | null;
  created_at: string;
  identity_reviews: WeddingIdentityReview[];
};

export type WeddingCapabilities = {
  mode: "disabled" | "demo" | "live";
  demo_only: boolean;
  providers: Record<string, { provider: string; configured: boolean; available: boolean; reason: string }>;
  ffmpeg_available: boolean;
  licensed_music_available: boolean;
  real_export_available: boolean;
};

export type WeddingCreationJob = {id: string; project_id: string; kind: "story" | "frame"; version: number; status: "queued" | "processing" | "completed" | "needs_action" | "cancelled"; error_code: string | null; completed_operations: number; total_operations: number; demo: boolean; result_story_id: string | null; created_at: string; updated_at: string};
export type WeddingPhotoQuality = {usable?: boolean; warnings?: string[]; demo_bypass?: boolean; face_count?: number; limitation?: string};
export type WeddingPhotoReadiness = {ready: boolean; demo_bypass: boolean; people: Array<{person_key: WeddingPersonKey; usable_count: number; required_count: number; primary_photo_asset_id: string | null; ready: boolean; warnings_unconfirmed: string[]; photos: Array<{asset_id: string; quality: WeddingPhotoQuality; confirmed_at: string | null}>}>};
export type WeddingMusicTrack = {track_id: string; title: string; license_id: string; license_version: string; valid_from: string; valid_until: string; perpetual_embedded_delivery: boolean; allowed_uses: string[]};

export type WeddingStoryDraft = {
  version: number;
  story_text: string;
  facts: Array<{ source_field: string; text: string }>;
  shots: Array<{
    shot_index: number;
    shot_type: WeddingShot["shot_type"];
    duration_ms: number;
    person_keys: WeddingPersonKey[];
    fact_indexes: number[];
    description: string;
    subtitle: string;
    creative_scene: boolean;
  }>;
};
