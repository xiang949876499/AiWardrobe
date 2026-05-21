export type Category = "top" | "bottom" | "outerwear" | "shoes" | "accessory";
export type GarmentStatus = "uploaded" | "extracting" | "tagging" | "pending_review" | "processing" | "ready" | "failed";
export type Occasion = "work" | "date" | "sport" | "formal" | "casual";

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

export type AuthResponse = {
  access_token: string;
  token_type: string;
  user: User;
};
