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
  duplicate_risks: Array<{ category: string; label: string; count: number; garment_ids: string[] }>;
  low_use_items: Array<Record<string, unknown>>;
  wardrobe_gaps: Array<{ category: Category; label: string; score: number; reason: string }>;
  avoid_categories: string[];
  suggested_categories: string[];
};

export type AuthResponse = {
  access_token: string;
  token_type: string;
  user: User;
};
