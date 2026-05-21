# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AiWardrobe is an AI-powered personal wardrobe management web app (MVP). Users upload garment photos, AI analyzes them (category, colors, style, material, season, fit, tags), users can correct the results, and the app generates outfit recommendations by occasion, season, and temperature.

## Tech Stack

- **Backend**: FastAPI + SQLAlchemy + Pydantic, Python >=3.11
- **Frontend**: React 18 + Vite + TypeScript, hand-written CSS (no framework)
- **Storage**: S3-compatible (MinIO in Docker, boto3 client), local filesystem fallback
- **Database**: PostgreSQL in production, SQLite for local dev
- **AI**: OpenAI-compatible multimodal API (`gpt-4o-mini`), with demo mode fallback
- **Infrastructure**: Docker Compose (postgres, minio, minio-init, backend, frontend)

## Commands

### Backend (`backend/`)

```powershell
cd backend
python -m pip install -e .[dev]          # install deps
python -m uvicorn app.main:app --reload   # dev server on :8000
python -m pytest                          # run all tests
```

### Frontend (`frontend/`)

```powershell
cd frontend
npm install         # install deps
npm run dev         # Vite dev server on :5173, proxies API to :8000
npm run build       # type-check + production build
npm test            # vitest tests
```

### Docker

```powershell
docker compose up --build   # all services (frontend, backend, postgres, minio)
```

Services: frontend `:5173`, backend `:8000`, MinIO console `:9001`, Postgres `:5432`.

## Configuration

Copy `.env.example` to `.env`. Key env vars:
- `DATABASE_URL` — defaults to local SQLite; set to Postgres URL for Docker/production
- `AI_DEMO_MODE` — defaults to `true`; set to `false` and configure `AI_BASE_URL`/`AI_API_KEY`/`AI_MODEL` for real AI
- `STORAGE_DRIVER` — `local` (default) or `s3` (requires `S3_ENDPOINT_URL`, `S3_BUCKET`, AWS credentials)
- `JWT_SECRET`, `SMTP_*` for email codes

## Architecture

### Backend (`backend/app/`)

- **`main.py`** — FastAPI app creation, middleware (CORS), router registration, startup event (DB tables)
- **`config.py`** — Pydantic `Settings` loading from env vars
- **`database.py`** — SQLAlchemy engine/session factory, `get_db` dependency
- **`models.py`** — 6 models: `User`, `EmailCode`, `Garment`, `UploadSession`, `Outfit`, `WeatherCache`
- **`schemas.py`** — Pydantic request/response schemas
- **`security.py`** — bcrypt password hashing, JWT create/decode (HS256, 7-day expiry), email code HMAC
- **`ai.py`** — Garment analysis + outfit generation via OpenAI-compatible API; `Analyzer` class with `analyze()` and `generate_outfit()` methods; falls back to demo mode (keyword matching from filename / random garment picking)
- **`storage.py`** — `LocalStorage` and `S3Storage` drivers behind a common interface; used for garment photo uploads and cropped images
- **`routers/`** — `auth.py` (register/login/email code), `garments.py` (CRUD + upload), `outfits.py` (generate/manual/list/favorite/fixed), `uploads.py` (batch photo upload with crop+analysis pipeline), `weather.py` (geolocation-based weather with caching)

Key patterns:
- All endpoints use `Depends(get_db)` for DB sessions and `Depends(get_current_user)` (JWT bearer) for auth
- Garments have a `review_status` workflow: AI results start as `pending_review`, user can `PATCH` to override, marking them `ready`
- Outfits store an `items` JSON column with `[{garment_id, image_url, category}]` and a `weather_snapshot` JSON

### Frontend (`frontend/src/`)

Single-page React app. Nearly all UI logic lives in `App.tsx` (~850 lines) with named components for each view:
- **App** — root: auth state, token persistence (`localStorage`), view routing, navigation bar
- **WardrobeView** — garment grid with category/search/filter controls
- **UploadView** — drag-and-drop file upload with processing progress
- **DetailView** — garment attribute editing (AI result confirmation/correction)
- **OutfitView** — AI generation + manual outfit creation via `ManualPicker`
- **HistoryView** — outfit list with favorite filtering
- **TryOnView** — placeholder cards (not functional in MVP)

Supporting files:
- **`api.ts`** — all backend calls via `fetch` with Bearer token; token stored in `localStorage` key `aiwardrobe_token`
- **`types.ts`** — TypeScript interfaces mirroring backend Pydantic schemas
- **`styles.css`** — ~785 lines, Plus Jakarta Sans, rose/pink brand (`#db2777`), responsive breakpoints at 800px (mobile bottom nav) and 430px (single-column grids), CSS Grid layouts, reduced-motion support

Vite proxies `/auth`, `/garments`, `/outfits`, `/static` to `localhost:8000`.

### Test Strategy

Backend tests use `TestClient` with SQLite temp DB + local storage + demo AI mode. A `conftest.py` fixture provides `client`, test DB, and a `login()` helper. Frontend tests use vitest + jsdom, mocking `fetch` globally and stubbing `navigator.geolocation`.

## Design Conventions (from MVP spec)

- Flat, light SaaS UI; rose/pink primary (`#db2777`), neutral surfaces
- Plus Jakarta Sans font, Lucide icons only, no emoji as UI icons
- Mobile bottom navigation (≤800px) → desktop sidebar (>800px)
- Cards: ≤8px border-radius, clear borders, no layout-shifting hover effects
- All form controls must have visible labels and keyboard-accessible focus states
