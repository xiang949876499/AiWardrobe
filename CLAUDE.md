# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AiWardrobe is an AI wardrobe assistant with buy-before-you-buy analysis, wardrobe and outfit tools, and an independent wedding MV studio. Users can analyze a product URL or image before purchase and use their wardrobe, preferences, reports, and outfit generation; wedding projects have separate participant consent and private media access.

## Tech Stack

- **Backend**: FastAPI + SQLAlchemy + Pydantic, Python >=3.11
- **Frontend**: React 18 + Vite + TypeScript, hand-written CSS (no framework)
- **Storage**: S3-compatible (MinIO in Docker, boto3 client), local filesystem fallback
- **Database**: PostgreSQL in production, SQLite for local dev
- **AI**: OpenAI-compatible multimodal API (`gpt-4o-mini`), with demo mode fallback
- **Infrastructure**: Docker Compose (postgres, minio, minio-init, backend, wedding-worker, frontend)

## Commands

### Backend (`backend/`)

```powershell
cd backend
uv venv .venv --python python
uv pip install --link-mode=copy --python .venv\Scripts\python.exe -e ".[dev]"
.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8031
python -m app.wedding_worker --poll-seconds 2
python -m pytest
```

### Frontend (`frontend/`)

```powershell
cd frontend
npm ci
npm run dev
npm run build
npm test
```

### Docker

```powershell
docker compose up --build
```

Node `>=24.14.0 <25`. Local Vite `:5174` proxies to API `:8031`. Compose >=2.24: frontend `:5173`, backend `:8000`, wedding worker, MinIO console `:9101`, Postgres `:5432`. API/worker share `.env` and the read-only licensed music directory.

## Configuration

Copy `.env.example` to `.env`. Key env vars:

- `DATABASE_URL` - Postgres URL for production/Docker; SQLite is allowed in development and tests
- `AI_DEMO_MODE` - defaults to `true`; set to `false` and configure `AI_BASE_URL`/`AI_API_KEY`/`AI_MODEL` for real AI
- `OUTFIT_AI_PROVIDER`, `DEEPSEEK_API_KEY`, `DEEPSEEK_BASE_URL`, `DEEPSEEK_MODEL` - outfit generation provider settings
- `SHOPPING_RECOMMENDATION_DEMO_MODE`, `TAOBAO_*` - wardrobe gap product search settings
- `STORAGE_DRIVER` - `local` (default) or `s3` (requires `S3_ENDPOINT_URL`, `S3_BUCKET`, AWS credentials)
- `WEDDING_*` - wedding MV provider, budget, worker, FFmpeg, and music settings; media mode defaults to `disabled`, and wedding S3 storage uses a private bucket distinct from wardrobe storage
- `JWT_SECRET`, `SMTP_*` for email codes

## Architecture

### Backend (`backend/app/`)

- **`main.py`** - FastAPI app creation, CORS/router registration and lifespan database/schema initialization
- **`config.py`** - Pydantic `Settings` loading from env vars
- **`database.py`** - SQLAlchemy engine/session factory, `get_db` dependency
- **`models.py`** - domain models for users, preferences, garments, purchase candidates, shopping recommendation runs/items, rate limits, upload sessions, outfits, weather cache, and wedding projects/assets/jobs/reviews
- **`schemas.py`** - Pydantic request/response schemas, including purchase analysis normalization for legacy rows
- **`security.py`** - bcrypt password hashing, JWT create/decode (HS256, 7-day expiry), email code HMAC
- **`ai.py`** - garment analysis and outfit generation via OpenAI-compatible API; falls back to demo mode
- **`storage.py`** - `LocalStorage` and `S3Storage` drivers behind a common interface
- **`wedding_service.py`, `wedding_providers.py`, `wedding_creation.py`, `wedding_worker.py`** - separate participant authorization/private storage, fixed provider adapters and durable story/frame/video jobs
- **`wedding_authoring.py`, `wedding_accounting.py`, `wedding_render_worker.py`, `wedding_retention.py`** - scoped original-photo/identity gates, music and immutable history, account receipts, leased FFmpeg rendering and reference-aware cleanup
- **`preferences.py`** - user preference context helper used by purchase analysis and outfit prompts
- **`purchase_analysis.py`** - deterministic purchase scoring, duplicate/idle risk, outfit potential, price guidance, and next actions
- **`wardrobe_report.py`** - wardrobe totals, distributions, scene coverage, duplicate risks, low-use items, and gaps
- **`shopping_recommendations.py`** - wardrobe-gap keywords, avoid categories, recommendation groups, candidate search, caching, and rate limits
- **`routers/`** - `auth.py`, `garments.py`, `uploads.py`, `outfits.py`, `purchase.py`, `shopping.py`, `reports.py`, `preferences.py`, and `weather.py`
- **`routers/wedding.py`** - wedding MV projects, consent, private assets, storyboard, generation, review, and export APIs

Key patterns:

- All user endpoints use `Depends(get_db)` for DB sessions and `Depends(get_current_user)` (JWT bearer) for auth
- Wedding endpoints use their own owner/participant actor scope. Both participants must have active portrait consent before media access or processing; revoked consent cancels renders and blocks export.
- Wedding writes lock project before child task/render; close transactions before provider/storage/FFmpeg I/O. Known provider IDs resume GET; unknown submissions retain reservations and require reconciliation rather than another paid POST.
- Preserve exact asset/person reviews and immutable render snapshots. Account financial receipts survive project deletion. Same-version draft edits use raw `If-Match` fingerprints; enqueue drains valid local saves first.
- PostgreSQL naive-time migration requires original writer timezone provenance; already-aware timestamps remain absolute. Photo quality uses per-thread CPU/OpenCL-off detection; see `docs/wedding-mv.md` for rationale and commands.
- Garments have a `review_status` workflow: AI results start as `pending_review`, user can `PATCH` to override, marking them `ready`
- Purchase candidates stay separate from garments until saved; shopping recommendation items can link to purchase candidates after analysis
- Outfits store an `items` JSON column with `[{garment_id, image_url, category}]` and a `weather_snapshot` JSON

### Frontend (`frontend/src/`)

Single-page React app. Nearly all UI logic lives in `App.tsx` with named components for each view:

- **App** - auth state, token persistence (`localStorage`), view routing, navigation bar, preference prompt trigger
- **HomeView** - default buy-before-you-buy entry for product URL/image analysis plus shortcuts to report/outfit
- **WardrobeView** - garment grid with category/search/filter controls and batch delete
- **UploadView** - single and batch drag-and-drop upload with processing progress
- **PurchaseAnalysisView** - URL/image purchase analysis, structured result sections, similar items, next actions, and save-to-wardrobe
- **ReportView** - wardrobe totals, distributions, duplicate risks, low-use items, gaps, avoid categories, and link to wardrobe gaps
- **ShoppingRecommendationsView** - wardrobe gaps, avoid guidance, recommendation groups, candidate analysis, and save flow
- **DetailView** - garment attribute editing (AI result confirmation/correction)
- **OutfitView** - AI generation by context, optional target garment, result feedback, and manual outfit creation via `ManualPicker`
- **HistoryView** - outfit list with favorite filtering
- **WeddingStudio.tsx** - independent wedding project flow; participant invite tokens stay in `sessionStorage` and do not replace the wardrobe login token
- **PreferencePrompt** - five-field lightweight personalization prompt after enough wardrobe context or a completed purchase analysis

Supporting files:

- **`api.ts`** - all backend calls via `fetch` with Bearer token; token stored in `localStorage` key `aiwardrobe_token`
- **`types.ts`** - TypeScript interfaces mirroring backend Pydantic schemas
- **`styles.css`** - Plus Jakarta Sans, rose/pink brand (`#db2777`), responsive breakpoints at 800px and 430px, CSS Grid layouts, reduced-motion support

Vite proxies `/v2`, legacy wardrobe routes and explicit wedding routes to `http://127.0.0.1:8031`; override with `VITE_BACKEND_TARGET`. Wedding draft/blob recovery must remain scoped to account, actor, project and content version. See `docs/wedding-mv.md` and the completion audit for actual contracts and external acceptance limits.

### Test Strategy

Backend tests use `TestClient` with SQLite temp DB + local storage + demo AI mode. A `conftest.py` fixture provides `client`, test DB, and a `login()` helper. Frontend tests use vitest + jsdom, mocking `fetch` globally and stubbing `navigator.geolocation`.

## Design Conventions

- Flat, light SaaS UI; rose/pink primary (`#db2777`), neutral surfaces
- Plus Jakarta Sans font, Lucide icons only, no emoji as UI icons
- Mobile bottom navigation (<=800px) and desktop sidebar (>800px)
- Cards: <=8px border-radius, clear borders, no layout-shifting hover effects
- All form controls must have visible labels and keyboard-accessible focus states
