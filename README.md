# AiWardrobe

AiWardrobe is an AI buy-before-you-buy wardrobe assistant with a FastAPI backend and a React + Vite mobile-first web frontend. The default product loop is: analyze a candidate item, explain whether it is worth buying, then use the wardrobe to improve future recommendations.

## What Is Included

- Email/password registration and login with JWT sessions.
- Single and batch garment upload endpoints, with client-side image compression.
- AI garment analysis through an OpenAI-compatible multimodal API, with demo fallback for local development.
- AI outfit recommendation through DeepSeek by default when `AI_DEMO_MODE=false` and `DEEPSEEK_API_KEY` is set.
- Manual garment correction for category, colors, style, material, season, fit, and tags.
- Outfit generation by occasion, season, temperature, and optional target garment.
- Current weather lookup through Open-Meteo with same-day location cache.
- Outfit history and favorite toggling.
- Purchase analysis from a product URL or product image, including structured scores, duplicate risk, idle risk, outfit potential, wardrobe similarity, price guidance, next actions, and saving a candidate into the wardrobe.
- Wardrobe report with category, color, style, scene coverage, duplicate risks, low-use items, wardrobe gaps, and avoid-category guidance.
- Wardrobe gaps that explain what is missing or overrepresented before showing Taobao/Tmall demo or configured product candidates.
- Lightweight user preferences for primary goal, scenes, styles, avoid types, and budget range.
- React UI following the agreed `ui-ux-pro-max` direction: flat, mobile-first, rose/pink brand accents, Lucide icons, labeled forms, and accessible focus states.

## Core APIs

- `POST /purchase/analyze` with `{ "url": "https://example.com/product/123" }` creates a `PurchaseCandidate` after extracting a likely product image and comparing it with ready wardrobe garments.
- `POST /purchase/analyze-image` accepts multipart `file` plus optional `product_url` when URL image extraction fails.
- `POST /purchase/candidates/{id}/save` converts a ready candidate into a normal ready `Garment` and marks the candidate as saved.
- `GET /reports/wardrobe` returns wardrobe totals, distributions, scene coverage, duplicate risks, low-use items, gaps, avoid categories, and suggested categories.
- `GET /preferences/me` returns the current user's lightweight style preferences.
- `PUT /preferences/me` updates `primary_goal`, `scenes`, `styles`, `avoid_types`, and `budget_range`.
- `POST /outfits/generate` can include `garment_id` or `purchase_candidate_id` to force a target item into the generated outfit.

`PurchaseCandidate` rows are separate from `Garment` rows until the user explicitly saves them.

## Wardrobe Gaps API

- `POST /shopping/recommendations` with `{ "target": "auto_gap", "refresh": false }` creates or reuses a recommendation run and returns Taobao/Tmall candidates.
- Supported targets: `auto_gap`, `work`, `date`, `sport`, `summer`, and `basics`.
- `POST /shopping/recommendations/items/{id}/analyze` deep-analyzes a pending recommendation item and links it to a `PurchaseCandidate`.
- Saving still uses `POST /purchase/candidates/{id}/save`.

Recommendation responses include `wardrobe_gaps`, `avoid_categories`, and `recommendation_groups` before item cards. Refreshes are limited per user, Taobao searches are limited globally, and item analysis is limited per user.

## Local Development

One-click start on Windows:

```powershell
.\start.bat
```

By default this starts the FastAPI backend and Vite frontend locally, then opens
the frontend. Other useful modes: `.\start.bat backend`, `.\start.bat frontend`,
and `.\start.bat docker` if you explicitly want the full Docker Compose stack.
The local backend uses `backend\aiwardrobe-local.db` (SQLite) and local upload
storage, so Docker/Postgres/MinIO are not required for basic development.

Backend:

```powershell
cd backend
uv venv .venv --python python
uv pip install --link-mode=copy --python .venv\Scripts\python.exe -e ".[dev]"
.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

Frontend:

```powershell
cd frontend
npm install
npm run dev
```

Open [http://localhost:5174](http://localhost:5174). The local frontend proxies API calls to [http://127.0.0.1:8031](http://127.0.0.1:8031).

## Docker Compose

```powershell
docker compose up --build
```

Services:

- Frontend: [http://localhost:5173](http://localhost:5173)
- Backend: [http://localhost:8000](http://localhost:8000)
- MinIO console: [http://localhost:9101](http://localhost:9101)
- Postgres: `localhost:5432`

By default Docker Compose runs with `AI_DEMO_MODE=true`. To use DeepSeek for outfit recommendation, copy `.env.example` to `.env`, set `AI_DEMO_MODE=false`, and provide:

```powershell
OUTFIT_AI_PROVIDER=deepseek
DEEPSEEK_API_KEY=your-deepseek-key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat
```

Garment image analysis can still use another OpenAI-compatible multimodal provider via `AI_BASE_URL`, `AI_API_KEY`, and `AI_MODEL`.

Shopping recommendations run in demo mode by default. To use configured Taobao/Tmall product search, set:

```powershell
SHOPPING_RECOMMENDATION_DEMO_MODE=false
TAOBAO_APP_KEY=your-taobao-app-key
TAOBAO_APP_SECRET=your-taobao-app-secret
TAOBAO_ADZONE_ID=your-taobao-adzone-id
TAOBAO_API_BASE_URL=https://eco.taobao.com/router/rest
```

Weather uses Open-Meteo by default and does not require an API key:

```powershell
WEATHER_PROVIDER=open_meteo
OPEN_METEO_BASE_URL=https://api.open-meteo.com
```

## Verification

Backend:

```powershell
cd backend
python -m pytest
```

Frontend:

```powershell
cd frontend
npm test
npm run build
```
