# AiWardrobe

AiWardrobe is an AI personal wardrobe MVP with a FastAPI backend and a React + Vite mobile-first web frontend.

## What Is Included

- Email/password registration and login with JWT sessions.
- Single and batch garment upload endpoints.
- AI garment analysis through an OpenAI-compatible multimodal API, with demo fallback for local development.
- Manual garment correction for category, colors, style, material, season, fit, and tags.
- Outfit generation by occasion, season, and temperature.
- Outfit history and favorite toggling.
- React UI following the agreed `ui-ux-pro-max` direction: flat, mobile-first, rose/pink brand accents, Lucide icons, labeled forms, and accessible focus states.

## Local Development

Backend:

```powershell
cd backend
python -m pip install -e .[dev]
python -m uvicorn app.main:app --reload
```

Frontend:

```powershell
cd frontend
npm install
npm run dev
```

Open [http://localhost:5173](http://localhost:5173). The frontend proxies API calls to [http://localhost:8000](http://localhost:8000).

## Docker Compose

```powershell
docker compose up --build
```

Services:

- Frontend: [http://localhost:5173](http://localhost:5173)
- Backend: [http://localhost:8000](http://localhost:8000)
- MinIO console: [http://localhost:9001](http://localhost:9001)
- Postgres: `localhost:5432`

By default Docker Compose runs with `AI_DEMO_MODE=true`. To use a real OpenAI-compatible provider, copy `.env.example` to `.env`, set `AI_DEMO_MODE=false`, and provide `AI_BASE_URL`, `AI_API_KEY`, and `AI_MODEL`.

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
