# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AiWardrobe is an AI wardrobe assistant with buy-before-you-buy analysis, wardrobe and outfit tools, and an independent wedding MV studio. Users can analyze a product URL or image before purchase and use their wardrobe, preferences, reports, and outfit generation; wedding projects have separate participant consent and private media access.

## Tech Stack

- **Backend**: FastAPI + SQLAlchemy + Pydantic, Python >=3.11
- **Frontend**: React 18 + Vite + TypeScript, hand-written CSS (no framework)
- **Storage**: S3-compatible (MinIO in Docker, boto3 client), local filesystem fallback
- **Database**: PostgreSQL in production; SQLite for development/tests only
- **AI**: Configurable vision and outfit providers; explicit demo mode for local/demo use, provider failures remain errors
- **Infrastructure**: Docker Compose (postgres, minio, minio-init, migrate, backend, worker, wedding-worker, frontend)

## Wedding production boundaries

Run `cd backend; python -m app.migrations upgrade` before production API/worker; startup verifies both wardrobe13 and wedding2 only, including TESTING mode. Upgrade steps must share the locked connection/transaction. Compose uses a successful migration service barrier. Invitation acceptance requires preview-bound disclosure hash; re-entry rotates session without new consent. Use explicit storyboard revisions for approved facts/scenes, preserving unchanged lineage/attempts. Shared frame/composition guards apply to LIVE even during tests; unknown provider outcomes retain reservations. See `docs/wedding-mv.md` for API contracts.

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

Docker services: frontend `:5173`, backend `:8000`, MinIO console `:9101`, Postgres `:5432`. `start.bat` starts independent API/wardrobe-worker/wedding-worker/frontend with shared configuration, frontend `:5174` and backend `:8031`; logs/process identities are in `.local-services/`. Use `start.bat stop-local` to stop recorded services safely.

Node `>=24.14.0 <25`; Compose >=2.24. API and both workers share `.env` and the read-only licensed music directory.

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

- **`main.py`** - FastAPI app creation, middleware (CORS), router registration, lifespan DB initialization
- **`config.py`** - Pydantic `Settings` loading from env vars
- **`database.py`** - SQLAlchemy engine/session factory, `get_db` dependency
- **`models.py`** - domain models for identity, garments, candidates/ownership receipts, durable tasks/provider operations, private assets, feedback/wear history, reports, outfits, and weather
- **`schemas.py`** - Pydantic request/response schemas, including purchase analysis normalization for legacy rows
- **`security.py`** - bcrypt password hashing, JWT create/decode (HS256, 7-day default expiry), secret-prefixed SHA-256 email code hashing
- **`ai.py`** - configurable provider calls and normalized analysis metadata; demo results are explicit and provider failures remain errors
- **`storage.py`** - `StorageService` selects local filesystem or S3 storage
- **`migrations.py`**, **`task_service.py`**, **`task_worker.py`** - explicit schema versions, durable/idempotent tasks, provider operation and stage metrics, and the independent worker
- **`asset_service.py`**, **`asset_access.py`**, **`asset_gc.py`** - private stored images, owner-authorized short-lived URLs, and reference-aware cleanup
- **`decision_context.py`**, **`feedback_service.py`**, **`routers/decision.py`**, **`routers/pagination.py`** - purchase revisions/snapshots, feedback and wear history, and cursor lists
- **`preferences.py`** - user preference context helper used by purchase analysis and outfit prompts
- **`purchase_analysis.py`** - deterministic purchase scoring, duplicate/idle risk, outfit potential, price guidance, and next actions
- **`wardrobe_report.py`** - wardrobe totals, distributions, scene coverage, duplicate risks, and gaps; `reports.py` adds 30-day wear coverage and only reports low-use garments when observation is sufficient
- **`shopping_recommendations.py`** - wardrobe-gap keywords, avoid categories, recommendation groups, candidate search, caching, and rate limits; recommendation item analysis runs through the durable task worker
- **`routers/`** - legacy auth/garment/upload/outfit/purchase/shopping/report/preference/weather routes plus v2 assets, decisions, tasks, and pagination
- **`wedding_service.py`, `wedding_providers.py`, `wedding_creation.py`, `wedding_worker.py`** - separate participant authorization/private storage, fixed provider adapters and durable story/frame/video jobs
- **`wedding_authoring.py`, `wedding_accounting.py`, `wedding_render_worker.py`, `wedding_retention.py`** - scoped original-photo/identity gates, music and immutable history, account receipts, leased FFmpeg rendering and reference-aware cleanup
- **`routers/wedding.py`** - wedding MV projects, consent, private assets, storyboard, generation, review, and export APIs

Key patterns:

- Wardrobe resource endpoints use `Depends(get_db)` for DB sessions and `Depends(get_current_user)` (JWT bearer) for auth
- New AI garment results remain pending confirmation; legacy routes stay available for compatibility
- Purchase candidates stay separate from trusted garments until explicitly confirmed/saved; use Idempotency-Key for task creation and do not blindly retry unknown provider submissions
- Legacy analysis creation uses `legacy_tasks.py` with the same worker/quotas and at most 20 seconds of synchronous waiting. Persist accepted workflow IDs before querying; recovery and cancelled cleanup remain read-only.
- Trusted garments require ready + confirmed + user_confirmed_at. Use `candidate_ownership.py`, unique `source_candidate_id`, and User-first locks for ownership/reanalysis. Schema 13 adds durable save/link receipts.
- Private images must use the owner-authorized asset routes; do not restore public static exposure

- Wedding endpoints use their own owner/participant actor scope. Both participants must have active portrait consent before media access or processing; revoked consent cancels renders and blocks export.
- Wedding writes lock project before child task/render; close transactions before provider/storage/FFmpeg I/O. Known provider IDs resume GET; unknown submissions retain reservations and require reconciliation rather than another paid POST.
- Preserve exact asset/person reviews and immutable render snapshots. Account financial receipts survive project deletion. Same-version draft edits use raw `If-Match` fingerprints; enqueue drains valid local saves first.
- PostgreSQL naive-time migration requires original writer timezone provenance; already-aware timestamps remain absolute. Photo quality uses per-thread CPU/OpenCL-off detection; see `docs/wedding-mv.md` for rationale and commands.
- Outfits store an `items` JSON column with `[{garment_id, image_url, category}]` and a `weather_snapshot` JSON

### Frontend (`frontend/src/`)

Single-page React app. `App.tsx` owns the shell and several views; purchase history and resumable tasks are separate components:

- **App** - auth state, token persistence (`localStorage`), view routing, navigation bar, preference prompt trigger
- **HomeView** - default buy-before-you-buy entry for product URL/image analysis plus shortcuts to report/outfit
- **WardrobeView** - garment grid with category/search/filter controls and batch delete
- **UploadView** - single/batch file selection, durable task progress, recovery and explicit confirmation
- **PurchaseAnalysisView** - URL/image purchase analysis, structured result sections, similar items, next actions, and save-to-wardrobe
- **ReportView** - wardrobe totals, distributions, duplicate risks, low-use items, gaps, avoid categories, and link to wardrobe gaps
- **ShoppingRecommendationsView** - wardrobe gaps, avoid guidance, recommendation groups, candidate analysis, and save flow
- **DetailView** - garment attribute editing (AI result confirmation/correction)
- **OutfitView** - AI generation by context, optional target garment, result feedback, and manual outfit creation via `ManualPicker`
- **HistoryView** - cursor-paged outfit history, favorites, and wear-event undo
- **PurchaseHistoryView**, **ResumablePurchasePanel** - candidate history/feedback and recoverable task status

- **WeddingStudio.tsx** - independent wedding project flow; participant invite tokens stay in `sessionStorage` and do not replace the wardrobe login token
- **PreferencePrompt** - five-field lightweight personalization prompt after enough wardrobe context or a completed purchase analysis

Supporting files:

- **`api.ts`** - all backend calls via `fetch` with Bearer token; token stored in `localStorage` key `aiwardrobe_token`
- **`types.ts`** - TypeScript interfaces mirroring backend Pydantic schemas
- **`styles.css`**, **`tokens.css`** - rose/pink brand (`#db2777`), responsive layouts and reduced-motion support; currently bundled Cormorant/Montserrat typography needs alignment with the design convention below

Vite proxies `/v2` and the legacy auth/garment/upload/outfit/purchase/shopping/report/preference/weather/static routes to `http://127.0.0.1:8031`; override with `VITE_BACKEND_TARGET`.

Production migrations run with `python -m app.migrations upgrade` before API/worker startup. Run a standalone worker with `python -m app.task_worker`. See `docs/api-v2.md`, `docs/architecture.md`, and `docs/deployment-and-migrations.md`.

Wedding routes are also proxied. Draft/blob recovery stays scoped to account, actor, project and content version; see `docs/wedding-mv.md`.

### Test Strategy

Backend tests use `TestClient` with SQLite temp DB + local storage + demo AI mode. A `conftest.py` fixture provides `client`, test DB, and a `login()` helper. Frontend tests use vitest + jsdom, mocking `fetch` globally and stubbing `navigator.geolocation`.

## Design Conventions

- Flat, light SaaS UI; rose/pink primary (`#db2777`), neutral surfaces
- Plus Jakarta Sans font, Lucide icons only, no emoji as UI icons
- Mobile bottom navigation (<=800px) and desktop sidebar (>800px)
- Cards: <=8px border-radius, clear borders, no layout-shifting hover effects
- All form controls must have visible labels and keyboard-accessible focus states
