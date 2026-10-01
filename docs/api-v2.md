# AiWardrobe v2 API guide

The API is served by FastAPI. Local development defaults to `http://127.0.0.1:8031`; Docker defaults to `http://localhost:8000`. Interactive OpenAPI docs are at `/docs`. Authenticated routes use `Authorization: Bearer <access_token>` from the existing login/register endpoints.

## Durable analysis tasks

Create a purchase URL task with a UUID `Idempotency-Key`:

```http
POST /v2/tasks/purchase-analysis
Authorization: Bearer <access_token>
Idempotency-Key: 78bc3ef4-a87e-4fda-a6bb-a9616608978e
Content-Type: application/json

{"url":"https://shop.example.com/products/item"}
```

The API returns `202 Accepted` and a task ID. Garment uploads use `POST /v2/tasks/garment-upload` with multipart `file` and optional `mode=single|split`. Recommendation image analysis uses `POST /v2/tasks/recommendation-analysis` with `item_id` and the idempotency header.

Poll `GET /v2/tasks/{task_id}` or list the account's tasks with `GET /v2/tasks?status=running&limit=50`. Task responses expose `status`, current `stage`, accumulated `stage_durations_ms`, safe provider-operation summaries, attempt count, result references, and structured error codes. Provider usage/cost appears only when explicitly returned and validated; the API does not estimate a provider's price.

`POST /v2/tasks/{task_id}/cancel` immediately cancels local processing. An accepted remote operation can still finish: the worker only queries its terminal state and releases capacity, without creating a garment or candidate from a late result.

`POST /v2/tasks/{task_id}/retry` requires a UUID `Idempotency-Key` and an empty JSON object. Only safely retryable failed tasks can transition to queued; active, cancelled, succeeded, or unresolved submissions return `409`. A successful retry receipt replays its original response even after the task completes.

RunningHub/ComfyUI accepted IDs are persisted before polling. The worker resumes read-only queries after restart, at least 30 seconds apart, for up to 15 minutes after acceptance. After that, `provider_recovery_timeout` requires action. `POST /v2/tasks/{task_id}/reconcile` with `{}` returns `202` to request one more read-only query; repeated requests within 30 seconds return `429` with `Retry-After`. A task with no queryable accepted ID returns `409 provider_reconciliation_unsupported`. Preserve it for operational verification; never blindly repeat an unknown paid request.

## Legacy analysis compatibility

The existing purchase, garment/upload, and shopping image-analysis creation routes use the same task/worker, provider quota, and capacity services. Legacy synchronous adapters wait at most 20 seconds. Completed work keeps the legacy resource shape; unfinished work returns `503` with `detail.code=analysis_pending`, `task_id` (and `task_ids` for a batch), and `Retry-After: 2`. Poll the task instead of starting a new analysis. Shopping recommendation orchestration may queue its first three analyses and return its existing run/item envelope immediately.

Legacy callers can send a UUID `Idempotency-Key`. Without one, identical normalized inputs reuse a task within a rolling 24-hour window; active or unresolved submissions remain reusable beyond that window. A successful candidate whose score is unavailable cannot satisfy the old integer-score schema: it returns `409 evidence_insufficient` with `task_id` and `candidate_id`. Read that candidate through v2, which supports null values. The default browser purchase flow submits v2 tasks directly.

## Candidate and history resources

| Method and path | Purpose |
|---|---|
| `GET /v2/purchase/candidates` | Cursor-paged purchase candidates; supports decision status |
| `GET/PATCH /v2/purchase/candidates/{id}` | Read or correct a candidate |
| `PATCH /v2/purchase/candidates/{id}/decision` | Save an explicit decision using the expected revision |
| `POST /v2/purchase/candidates/{id}/save` | Confirm ownership and link one wardrobe garment |
| `POST /v2/purchase/candidates/{id}/link-owned-garment` | Explicitly reconcile ownership with an existing confirmed garment |
| `POST /v2/purchase/candidates/{id}/reanalyze` | Queue analysis against the current decision context |
| `GET /v2/garments`, `GET /v2/outfits` | Cursor-paged, server-filtered wardrobe/history lists |
| `GET/POST/DELETE /v2/feedback` | List, create, or revoke account-scoped decision feedback |
| `GET/POST/DELETE /v2/wear-events` | List, record, or revoke wear history |

Paginated lists default to 50 records and accept `limit` from 1 to 100. Responses contain `items`, `next_cursor`, and `has_more`. Send `next_cursor` unchanged to continue. A cursor is bound to the account and filters; start again from the first page when a filter changes. The exact schemas and additional query parameters are available at `/docs`.

Save/link require a UUID `Idempotency-Key`, `expected_revision`, and `confirm_owned=true`; link also requires `garment_id`. Replay uses semantic input (candidate/garment IDs, confirmation, explicit tags), excluding `expected_revision`, and returns the stored response. Changed semantic input under the same key returns `409 idempotency_conflict`. An already-linked save returns the original garment. `legacy_unlinked` must be explicitly linked first (`409 ownership_link_required`); deleting a linked garment keeps its ID as a `removed` tombstone, and later save/link returns `409 owned_garment_removed`.

Purchase scores, unavailable score dimensions, risk detail, and suggested prices may be null. Render evidence insufficiency explicitly. Outfit ideas contain a candidate reference plus real, confirmed, owner-bound wardrobe references; zero ideas is valid. Current reads hydrate authorized image URLs and omit ideas with invalid/deleted references. Stored snapshots contain stable IDs rather than expiring signatures; context or purchase-rule version changes mark old snapshots stale.

## Private asset URLs

Asset references are returned only after owner authorization. `GET /v2/assets/{asset_id}/access` issues a short-lived access URL. The returned content URL can be used by a browser `<img>` until it expires; request a fresh URL through an authenticated endpoint after expiry. A signature is scoped to its owner and asset variant.

## Errors and safe retries

Errors use stable `detail.code` values with the HTTP status. Common cases include `401` authentication failure, `404` inaccessible resource, `409` revision/idempotency conflict, `413` image limits, `422` invalid input/cursor, `429` rate limit with `Retry-After`, and `502`/`503` provider or availability failures. Do not parse English error messages to make retry decisions. Avoid logging bearer tokens, submitted image bytes, or raw provider responses.
