# AiWardrobe MVP Product and UI/UX Spec

## Goal

AiWardrobe is an AI personal wardrobe web app. Users register and log in with email/password, upload garment photos, review AI-generated attributes, manually correct them, and generate outfit recommendations by occasion, season, and temperature.

## MVP Scope

- Email/password registration, login, and JWT session handling.
- Cloud wardrobe with garment photos and editable metadata.
- Single and batch upload API support.
- AI garment analysis for category, colors, style, material, season, fit, confidence, and tag suggestions.
- Manual correction with user edits taking priority over AI results.
- Outfit generation for work, date, sport, formal, and casual occasions.
- Outfit explanation, history, and favorite state.
- Purchase analysis from a generic product URL, with product image extraction, AI garment tagging, wardrobe comparison, deterministic recommendation scoring, manual product image fallback, and save-to-wardrobe.
- Shopping recommendations for Taobao/Tmall through demo data or configured application-level Taobao credentials, with wardrobe gap keywords, cached candidate runs, item analysis, rate limits, and save-to-wardrobe through purchase candidates.

Out of scope for v1: payments, social/community, automatic purchase, platform-specific shopping scraping outside official/configured APIs, price tracking, virtual try-on, outfit calendar, shared wardrobes, WeChat mini-program implementation, complex brand recognition, and background removal.

## Architecture

- Backend: FastAPI, SQLAlchemy, Postgres in production, SQLite-compatible local tests.
- Frontend: React + Vite + TypeScript, mobile-first responsive web.
- Storage: S3-compatible object storage, with local storage fallback for development and tests.
- AI: OpenAI-compatible multimodal API, with demo fallback when no API key is configured.
- Purchase candidates: URL-derived or manually uploaded product images are stored separately as `PurchaseCandidate` records until the user saves them as ready `Garment` records.
- Shopping recommendations: `ShoppingRecommendationRun` and `ShoppingRecommendationItem` records track target, keywords, cache reuse, analysis status, linked purchase candidates, and per-item failures.

## UI/UX Direction

- Flat, light SaaS interface with rose/pink primary accents, neutral surfaces, and restrained gold CTA use.
- Plus Jakarta Sans typography.
- Lucide icons only; no emoji UI icons.
- Mobile bottom navigation for wardrobe, upload, outfit, and history.
- Desktop sidebar navigation.
- All form controls have labels, visible focus states, and keyboard-accessible actions.
- Cards keep 8px or smaller radius, clear borders, and no layout-shifting hover effects.

## Acceptance

- A new user can register with email/password and reach the wardrobe.
- An existing user can log in with email/password.
- A user can upload garment photos, receive AI attributes, and save manual corrections.
- A user can filter wardrobe items by category, tag, color, or season.
- A user with enough ready garments can generate an outfit, save it as favorite, and view it in history.
- A user can submit a product URL, receive a recommend/consider/skip purchase analysis, see similar wardrobe items, and save the candidate into the wardrobe.
- If URL image extraction fails, the UI offers manual product image upload and continues analysis.
- A user can open shopping recommendations, choose a target, fetch Taobao/Tmall candidates, analyze pending items, see recommendation scores and similar wardrobe items, and save useful analyzed items into the wardrobe.
- Recommendation refreshes, Taobao searches, and item analysis are rate-limited with readable UI feedback.
- The UI works at mobile and desktop widths without horizontal overflow.
