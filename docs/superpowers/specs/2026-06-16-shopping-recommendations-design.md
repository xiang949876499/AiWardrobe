# Shopping Recommendations Design

## Goal

Add a shopping recommendation page to AiWardrobe. The user clicks a recommendation entry, the system searches Taobao/Tmall products through the application's configured Taobao Open Platform/Taobao affiliate credentials, compares candidates with the user's current wardrobe, and helps the user save useful items into the wardrobe.

This feature complements the existing Purchase Analysis flow:

- Purchase Analysis: the user supplies a product URL and asks whether it is worth buying.
- Shopping Recommendations: the system proactively searches Taobao/Tmall for products that can fill wardrobe gaps.

## Product Decisions

- First platform: Taobao/Tmall.
- Account model: application-level Taobao Open Platform/Taobao affiliate configuration, not per-user Taobao account binding.
- Recommendation model: hybrid goal selection. The default target is automatic wardrobe gap detection, with user-selectable targets such as work, date, sport, summer, and basics.
- Implementation approach: Taobao search adapter plus a cached recommendation candidate pool. Search results are returned quickly, while only the highest-priority candidates are deeply analyzed by VL tagging and purchase scoring.

## Scope

In scope:

- A new "Shopping Recommendations" page in the app.
- Backend Taobao/Tmall product search through a provider adapter.
- Demo mode that returns stable mock Taobao products when Taobao credentials are not configured.
- Wardrobe gap keyword generation from ready garments.
- User target filters:
  - `auto_gap`
  - `work`
  - `date`
  - `sport`
  - `summer`
  - `basics`
- Recommendation run and item persistence.
- Local cache reuse for recent keyword/product results.
- Rate limiting for user refreshes, global Taobao search, and per-user item analysis.
- Deep analysis for top candidates using existing `AiService.analyze_garment` and `analyze_purchase`.
- Saving analyzed recommendations through the existing `PurchaseCandidate` to `Garment` path.

Out of scope for this MVP:

- Per-user Taobao account binding.
- Reading user Taobao purchase history or browsing history.
- Checkout or automatic purchase.
- Platform-specific scraping outside official or configured API access.
- Browser automation for Taobao pages.
- Price tracking, coupons, inventory tracking, or order attribution.
- Multi-platform recommendation beyond Taobao/Tmall.

## User Flow

1. The user opens the new "Shopping Recommendations" page.
2. The page defaults to `auto_gap`.
3. The user clicks "Get Recommendations".
4. The backend reads the user's ready wardrobe and generates recommendation keywords.
5. The backend checks rate limits and recent cached results.
6. The backend searches Taobao/Tmall through the provider adapter or demo client.
7. The system stores a `ShoppingRecommendationRun` and `ShoppingRecommendationItem` rows.
8. The first few candidates are deeply analyzed:
   - download product image
   - run existing VL garment tagging
   - compare with ready wardrobe using existing purchase scoring
   - create or link a `PurchaseCandidate`
9. The UI displays a product stream:
   - analyzed items with recommendation, score, reason, tags, and similar wardrobe items
   - pending items with title, image, price, shop, and "Analyze this item"
10. The user can:
   - change target filters
   - refresh within rate limits
   - analyze a pending item
   - save an analyzed item to the wardrobe
   - open the Taobao/Tmall product link

## Backend Design

### Module Boundaries

`shopping_recommendations`

- Owns recommendation run orchestration.
- Reads ready wardrobe state.
- Generates search keywords for `auto_gap` and explicit targets.
- Creates recommendation runs and items.
- Selects top candidates for deep analysis.
- Converts analyzed recommendation items into linked `PurchaseCandidate` records.

`taobao_client`

- Encapsulates Taobao Open Platform/Taobao affiliate request signing and response parsing.
- Exposes one project-level product search method.
- Converts provider responses into a normalized `CommerceProduct`.
- Does not expose app secrets to the frontend.

`rate_limit`

- Owns database-backed rate limit windows.
- Supports:
  - per-user recommendation refresh limit
  - global Taobao search limit
  - per-user item analysis limit
- Returns reset timestamps so the UI can explain waiting time.

### Normalized Product Type

`CommerceProduct`:

- `platform`: `taobao`
- `platform_item_id`
- `title`
- `image_url`
- `price`
- `shop_name`
- `product_url`
- `raw`

### Data Model

Add `ShoppingRecommendationRun`:

- `id`
- `user_id`
- `target`
- `keywords`
- `status`
- `error_code`
- `cache_hit`
- `rate_limit`
- `created_at`
- `updated_at`

`target` values:

- `auto_gap`
- `work`
- `date`
- `sport`
- `summer`
- `basics`

`status` values:

- `running`
- `ready`
- `failed`
- `rate_limited`

Add `ShoppingRecommendationItem`:

- `id`
- `run_id`
- `user_id`
- `platform`
- `platform_item_id`
- `title`
- `image_url`
- `price`
- `shop_name`
- `product_url`
- `raw`
- `analysis_status`
- `purchase_candidate_id`
- `recommendation`
- `score`
- `reason_summary`
- `created_at`
- `updated_at`

`analysis_status` values:

- `pending_analysis`
- `analyzing`
- `analyzed`
- `failed`

The item links to `PurchaseCandidate` only after deep analysis succeeds. Saving to wardrobe continues to use `POST /purchase/candidates/{id}/save`.

### API

`POST /shopping/recommendations`

Request:

```json
{
  "target": "auto_gap",
  "refresh": false
}
```

Response:

```json
{
  "id": "run-id",
  "target": "auto_gap",
  "keywords": ["summer work skirt", "black low heel shoes"],
  "cache_hit": false,
  "rate_limit": {
    "remaining_refreshes": 2,
    "reset_at": "2026-06-16T16:40:00Z"
  },
  "items": [
    {
      "id": "item-id",
      "platform": "taobao",
      "platform_item_id": "123",
      "title": "Black work skirt",
      "image_url": "https://example.com/item.jpg",
      "price": "129.00",
      "shop_name": "Example shop",
      "product_url": "https://example.com/item",
      "analysis_status": "analyzed",
      "purchase_candidate_id": "candidate-id",
      "recommendation": "recommend",
      "score": 82,
      "reason_summary": "Fills a work bottom gap and pairs with several existing tops."
    }
  ]
}
```

Behavior:

- With `refresh=false`, the backend may return recent cached recommendation items for the same user and target.
- With `refresh=true`, the backend attempts a new Taobao search after checking user and global rate limits.
- The first three candidates are deep-analyzed automatically when possible.

`POST /shopping/recommendations/items/{item_id}/analyze`

Behavior:

- Checks item ownership.
- Checks per-user analysis rate limit.
- Downloads the product image.
- Runs existing garment VL analysis.
- Runs existing purchase scoring against ready wardrobe items.
- Creates or updates the linked `PurchaseCandidate`.
- Updates the recommendation item to `analysis_status="analyzed"`.

`POST /purchase/candidates/{id}/save`

- Reused unchanged from the purchase analysis flow.

### Taobao Integration

Configuration:

- `TAOBAO_APP_KEY`
- `TAOBAO_APP_SECRET`
- `TAOBAO_ADZONE_ID`
- `TAOBAO_API_BASE_URL`
- `SHOPPING_RECOMMENDATION_DEMO_MODE`

Official documentation references:

- Taobao affiliate material search API: https://developer.alibaba.com/docs/api.htm?apiId=35896
- Taobao Open Platform authorization background: https://open.alitrip.com/doc2/detail.htm?articleId=101776&docType=1&treeId=1

The MVP should not require per-user Taobao authorization. It should use application-level credentials for product search. If credentials are absent and demo mode is enabled, the provider returns stable mock products.

### Keyword Generation

Only ready wardrobe garments are considered.

`auto_gap` generation:

- Count ready garments by category.
- Prefer categories with the lowest counts.
- Consider underrepresented colors, style, and season.
- Generate up to five keywords.

Example:

- User has many tops, one bottom, no work shoes.
- Keywords:
  - "work skirt"
  - "black low heel shoes"
  - "summer lightweight trousers"

Explicit target generation:

- `work`: combine low-count categories with work/casual-work terms.
- `date`: combine missing categories with softer or polished style terms.
- `sport`: prioritize shoes, tops, bottoms with sport terms.
- `summer`: prioritize breathable material and summer terms.
- `basics`: prioritize neutral colors and versatile categories.

### Rate Limiting

MVP database-backed limits:

- Per-user recommendation refresh: 3 refreshes per 10 minutes.
- Global Taobao search: 30 searches per minute.
- Per-user item analysis: 5 item analyses per minute.

Error codes:

- `recommendation_rate_limited`
- `taobao_rate_limited`
- `analysis_rate_limited`

Responses should include `reset_at` when possible.

### Caching

Recommendation cache:

- Same user and same target can reuse recent runs when `refresh=false`.
- Suggested TTL: 30 minutes.

Product analysis cache:

- Same `platform` and `platform_item_id` should reuse existing analyzed item or linked candidate where possible.
- Do not repeat VL analysis for the same product image unless explicitly refreshed in a later version.

## Frontend Design

Add a new navigation entry: "Shopping Recommendations" in Chinese UI wording.

The page uses the selected B layout: product stream.

Page states:

- Empty: target segmented control and "Get Recommendations" button.
- Loading: show search/analyze progress.
- Success: product stream.
- Rate limited: show a visible wait message and reset time.
- Partial failure: show available items and per-item failure states.
- Empty recommendation: show a no-results state and suggest another target.

Product card fields:

- product image
- title
- price
- shop name
- recommendation badge:
  - `Recommended`
  - `Consider`
  - `Skip`
  - `Pending analysis`
  - `Analysis failed`
- score when analyzed
- reason summary when analyzed
- similar wardrobe items when analyzed
- actions:
  - Analyze this item
  - Add to wardrobe
  - View on Taobao

The UI should avoid nested cards. Each product is one card in the stream. Analysis details expand inside the same card.

## Error Handling

Recoverable errors:

- Taobao credentials missing in non-demo mode: `taobao_not_configured`
- Taobao search failed: `taobao_fetch_failed`
- Recommendation refresh limited: `recommendation_rate_limited`
- Global Taobao limit reached: `taobao_rate_limited`
- Item analysis limited: `analysis_rate_limited`
- Product image download failed: item-level `analysis_status="failed"`
- VL analysis failed: item-level `analysis_status="failed"`

Hard errors:

- unauthenticated request
- recommendation run not found
- recommendation item not found
- recommendation item belongs to another user

When a single item fails analysis, the rest of the recommendation response should still be usable.

## Testing Plan

Backend:

- `auto_gap` generates keywords from ready wardrobe category gaps.
- Explicit targets generate stable keywords.
- Demo Taobao client returns normalized `CommerceProduct` values.
- Taobao signing and request parameter construction are deterministic.
- Recommendation API creates a run and item rows.
- Recommendation API reuses cache when `refresh=false`.
- Recommendation API enforces per-user refresh limit.
- Recommendation API enforces global Taobao search limit.
- Top candidates are selected for deep analysis.
- Single item analysis creates or links a `PurchaseCandidate`.
- Existing candidate save endpoint creates a ready `Garment`.
- A failed item analysis does not fail the whole recommendation run.

Frontend:

- User opens Shopping Recommendations and sees target controls.
- User clicks Get Recommendations and sees product candidates.
- Switching target requests a new recommendation target.
- Pending items show Analyze this item.
- Analyzed items show recommendation, score, reason, and similar wardrobe items.
- Rate limit errors show waiting copy.
- Add to wardrobe calls existing candidate save flow and updates wardrobe state.
- View on Taobao renders as an outbound product link.

## Implementation Order

1. Backend models and schema.
2. `CommerceProduct` type and Taobao demo client.
3. Wardrobe gap keyword generation.
4. Database-backed rate limit module.
5. `POST /shopping/recommendations`.
6. `POST /shopping/recommendations/items/{item_id}/analyze`.
7. Frontend API types and client methods.
8. Frontend Shopping Recommendations navigation and product stream page.
9. Environment documentation and `.env.example`.
10. Verification with backend tests, frontend tests, and frontend build.
