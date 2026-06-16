# Purchase Analysis Design

## Goal

Add a purchase analysis feature to AiWardrobe. The user enters a product URL, the system extracts the clothing item from the product page, compares it with the user's current wardrobe, and returns a clear purchase recommendation.

The MVP recommendation uses option 2: deterministic rule scoring for the decision, with an LLM-generated explanation for readability.

## User Flow

1. The user opens a new Purchase Analysis entry in the app.
2. The user pastes a product URL and starts analysis.
3. The backend fetches the page and extracts a likely product image.
4. The backend downloads the image and runs existing garment VL tagging.
5. The backend compares the candidate garment with the user's ready wardrobe items.
6. The system returns:
   - product image
   - extracted garment tags
   - similar wardrobe items
   - wardrobe gaps or pairing opportunities
   - recommendation: `recommend`, `consider`, or `skip`
   - score and explanation
7. The user may save the candidate into the wardrobe. Saving creates a normal ready `Garment`.

If URL image extraction fails, the API returns a recoverable failure and the UI offers manual product image upload as a fallback.

## Scope

In scope:
- Generic product URL analysis.
- Product image extraction from standard metadata and page images.
- VL tagging using the existing `AiService.analyze_garment`.
- Similarity comparison against the user's `ready` garments.
- Rule-based purchase decision.
- DeepSeek or configured outfit LLM explanation, with deterministic fallback.
- Save candidate to wardrobe.

Out of scope for this MVP:
- Platform-specific scraping for Taobao, JD, Xiaohongshu, Dewu, or login-only pages.
- Price tracking.
- Coupons or shopping checkout.
- Browser automation for heavily dynamic pages.
- Vector embeddings.
- Automatic purchase.

## Backend Design

### New Module Boundaries

`product_extraction`
- Fetches the product URL.
- Parses HTML.
- Selects image candidates from:
  - `og:image`
  - `twitter:image`
  - common product image meta tags
  - large image tags in the page
- Resolves relative URLs.
- Downloads the selected image.
- Returns image bytes, content type, source image URL, title, and site domain.

`purchase_analysis`
- Owns scoring and recommendation.
- Converts candidate VL tags into a comparable profile.
- Compares the candidate to the user's ready wardrobe.
- Produces top similar items and wardrobe gap signals.
- Calls AI explanation service after the deterministic result is computed.

`purchase router`
- `POST /purchase/analyze`
- `POST /purchase/candidates/{id}/save`

### Data Model

Add `PurchaseCandidate`:
- `id`
- `user_id`
- `product_url`
- `source_image_url`
- `image_url`
- `image_key`
- `thumbnail_url`
- `title`
- `domain`
- `category`
- `colors`
- `style`
- `material`
- `season`
- `fit`
- `tags`
- `ai_result`
- `ai_confidence`
- `similar_items`
- `recommendation`
- `score`
- `reason_summary`
- `analysis`
- `status`
- `created_at`
- `updated_at`

`status` values:
- `analyzing`
- `ready`
- `failed`
- `saved`

The candidate is separate from `Garment` until the user explicitly saves it.

### API

`POST /purchase/analyze`

Request:
```json
{
  "url": "https://example.com/product/123"
}
```

Response:
```json
{
  "id": "candidate-id",
  "product_url": "https://example.com/product/123",
  "source_image_url": "https://example.com/product.jpg",
  "image_url": "/static/uploads/purchase/candidate.jpg",
  "title": "Product title",
  "domain": "example.com",
  "category": "top",
  "colors": ["black"],
  "style": "casual",
  "material": "cotton",
  "season": ["summer"],
  "fit": "standard",
  "tags": ["T-shirt", "casual"],
  "similar_items": [
    {
      "garment_id": "garment-id",
      "image_url": "/static/uploads/existing.jpg",
      "similarity": 0.86,
      "matched_reasons": ["same category", "similar color", "casual style"]
    }
  ],
  "recommendation": "consider",
  "score": 68,
  "reason_summary": "Similar to one existing black casual top, but useful for summer outfits.",
  "analysis": {
    "duplicate_score": 74,
    "wardrobe_gap_score": 45,
    "pairing_score": 72,
    "decision_factors": []
  },
  "status": "ready"
}
```

`POST /purchase/candidates/{id}/save`

Creates a ready `Garment` from the candidate and marks the candidate as `saved`.

### URL Extraction Rules

The product fetcher uses a normal HTTP client with a browser-like user agent and timeout. It does not execute JavaScript in the MVP.

Image selection priority:
1. `meta[property="og:image"]`
2. `meta[name="twitter:image"]`
3. JSON-LD Product image if easy to parse
4. visible `img` candidates with product-like sizes or names

If no valid image is found, return an error code such as `product_image_not_found`. The UI then asks the user to upload a product image manually.

## Scoring Design

Only ready wardrobe garments are considered.

Similarity score:
- category match: 30
- overlapping main colors: 20
- style similarity: 15
- material similarity: 10
- season overlap: 10
- fit similarity: 5
- tag overlap: 10

Duplicate interpretation:
- `>= 80`: very similar
- `60-79`: somewhat similar
- `< 60`: distinct

Wardrobe gap score:
- Higher if the user has few items in the same category.
- Higher if the color/style/season is underrepresented.
- Lower if the wardrobe already has many near duplicates.

Pairing score:
- For tops: count compatible bottoms, shoes, and outerwear.
- For bottoms: count compatible tops and shoes.
- For shoes/accessories/bags: count broad usability across existing outfits.

Recommendation:
- `recommend`: score `>= 75`, low duplicate risk, useful gap or high pairing value.
- `consider`: score `50-74`, some value but either duplicate risk or limited pairing.
- `skip`: score `< 50`, high duplicate risk or low pairing value.

The LLM explanation receives only the computed facts and must not override the rule-based recommendation. If the LLM fails, return the deterministic reason summary.

## Frontend Design

Add a Purchase Analysis navigation entry. In the Chinese UI, use the same wording as the rest of the app for purchase analysis.

Page states:
- empty form: URL input and analyze button
- loading: fetching product and analyzing clothing
- success: product image, tags, recommendation, similar items, pairing/gap reasons
- recoverable failure: manual product image upload fallback
- save success: candidate is added to wardrobe

The page should follow the existing mobile-first SaaS UI:
- clear label for URL input
- compact result cards
- no nested cards
- image alt text
- visible loading and error states

## Error Handling

Recoverable errors:
- invalid URL
- page fetch failed
- product image not found
- image download failed
- VL analysis failed

The UI should show a specific message and offer manual upload when the URL path cannot produce an image.

Hard errors:
- unauthenticated request
- candidate not found
- candidate belongs to another user

## Testing Plan

Backend:
- URL metadata extraction prefers `og:image`.
- relative image URLs resolve correctly.
- invalid/no-image product pages return recoverable errors.
- candidate analysis stores a candidate, not a garment.
- similarity scoring identifies near duplicates.
- gap and pairing scores influence `recommend`, `consider`, and `skip`.
- save candidate creates a ready garment.
- LLM explanation failure falls back to deterministic reason text.

Frontend:
- user can enter a URL and see an analysis result.
- similar wardrobe items render.
- `recommend`, `consider`, and `skip` states render distinctly.
- URL extraction failure shows manual upload fallback.
- saving candidate updates wardrobe state.

End to end:
- user logs in
- has at least three ready garments
- submits a product URL
- receives recommendation with similar items
- saves the candidate to wardrobe
- sees it in the wardrobe page
