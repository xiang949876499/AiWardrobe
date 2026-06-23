# AiWardrobe MVP Product and UI/UX Spec

## Goal

AiWardrobe is an AI buy-before-you-buy wardrobe web app. Users register and log in, analyze a product URL or product image before purchase, get a structured buy/consider/skip recommendation, and improve those recommendations by adding wardrobe items and lightweight style preferences.

## MVP Scope

- Email/password registration, login, and JWT session handling.
- Cloud wardrobe with garment photos, editable metadata, and reportable wardrobe coverage.
- Single and batch upload API support, with frontend preview/progress and client-side image compression.
- AI garment analysis for category, colors, style, material, season, fit, confidence, and tag suggestions.
- Manual correction with user edits taking priority over AI results.
- Outfit generation for work, date, sport, formal, and casual occasions, including optional target garment selection.
- Outfit explanation, history, favorite/fixed state, delete, and local useful/not-useful feedback.
- Purchase analysis from a generic product URL or product image, with product image extraction, AI garment tagging, wardrobe comparison, structured scoring, duplicate risk, idle risk, outfit potential, scene match, suggested price, next actions, manual product image fallback, and save-to-wardrobe.
- Wardrobe report with totals, category/color/style distributions, scene coverage, duplicate risks, low-use items, gaps, avoid categories, and suggested categories.
- Wardrobe gaps for Taobao/Tmall through demo data or configured application-level Taobao credentials, with gap explanations, avoid guidance, recommendation groups, cached candidate runs, item analysis, rate limits, and save-to-wardrobe through purchase candidates.
- Lightweight user preferences covering primary goal, scenes, styles, avoid types, and budget range.

Out of scope for v1: payments, social/community, automatic purchase, platform-specific shopping scraping outside official/configured APIs, price tracking, virtual try-on, outfit calendar, shared wardrobes, WeChat mini-program implementation, complex brand recognition, and background removal.

## Architecture

- Backend: FastAPI, SQLAlchemy, Postgres in production, SQLite-compatible local tests.
- Frontend: React + Vite + TypeScript, mobile-first responsive web.
- Storage: S3-compatible object storage, with local storage fallback for development and tests.
- AI: OpenAI-compatible multimodal API, with demo fallback when no API key is configured.
- Purchase candidates: URL-derived or manually uploaded product images are stored separately as `PurchaseCandidate` records until the user saves them as ready `Garment` records. `PurchaseCandidateResponse.analysis` contains structured decision fields and backfills legacy rows.
- Shopping recommendations: `ShoppingRecommendationRun` and `ShoppingRecommendationItem` records track target, keywords, cache reuse, analysis status, wardrobe gaps, avoid categories, recommendation groups, linked purchase candidates, and per-item failures.
- Preferences: `UserPreference` stores one lightweight preference row per user and is injected into purchase analysis and outfit prompts when available.

## UI/UX Direction

- Flat, light SaaS interface with rose/pink primary accents, neutral surfaces, and restrained gold CTA use.
- Plus Jakarta Sans typography.
- Lucide icons only; no emoji UI icons.
- Mobile bottom navigation for home, wardrobe, outfit, report, and history.
- Desktop sidebar navigation.
- All form controls have labels, visible focus states, and keyboard-accessible actions.
- Cards keep 8px or smaller radius, clear borders, and no layout-shifting hover effects.

## Acceptance

- A new user can register with email/password and reach the wardrobe.
- An existing user can log in with email/password.
- A user can upload garment photos, receive AI attributes, and save manual corrections.
- A user can filter wardrobe items by category, tag, color, or season.
- A user with enough ready garments can generate an outfit, save it as favorite, and view it in history.
- A user can generate an outfit around a specified owned garment.
- A user can submit a product URL or product image, receive a recommend/consider/skip purchase analysis, see total score, score breakdown, risks, similar wardrobe items, outfit ideas, price guidance, and next actions, then save the candidate into the wardrobe.
- If URL image extraction fails, the UI offers manual product image upload and continues analysis.
- A user can view wardrobe report totals, distributions, duplicate risks, low-use items, gaps, and avoid categories.
- A user can open wardrobe gaps, choose a target, see what is missing or overrepresented before product cards, fetch Taobao/Tmall candidates, analyze pending items, see recommendation scores and similar wardrobe items, and save useful analyzed items into the wardrobe.
- A user is prompted for lightweight preferences after enough wardrobe context or a completed purchase analysis, and can save or skip the prompt.
- Recommendation refreshes, Taobao searches, and item analysis are rate-limited with readable UI feedback.
- The UI works at mobile and desktop widths without horizontal overflow.
