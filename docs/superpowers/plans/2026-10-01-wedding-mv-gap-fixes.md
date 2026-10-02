# Wedding MV completion gap fixes

> Historical feature-stage plan: completed locally on `codex/ai-wedding-mv`. The user subsequently authorized full local `main` integration and review on 2026-10-02. Current merge, corrections and validation: [main integration review](../../reviews/2026-10-02-main-integration-code-review.md). Keep the original D checkout/private state untouched; no remote or paid actions are authorized.

Goal: implement the missing P0 product logic identified in `docs/wedding-mv-completion-audit-2026-10-01.md`, and replace assumptions with executable evidence. Keep the original worktree untouched.

## Global constraints

- Work only in `C:/Users/94987/.codex/worktrees/ai-wedding-mv/AiWardrobe`; feature work used `codex/ai-wedding-mv`, later explicit user authorization permits local `main` integration/corrections. No pushing, paid calls, remote agents, publication, or credential changes.
- Private media access always checks project membership, current consent, expiry and render state. Lock parent project before child job/render when both locks are needed. Never hold database transactions open during remote calls or FFmpeg.
- Fixed production baseline: `doubao-seed-2-0-pro-260215`, `doubao-seedream-4-5-251128`, `wan2.7-r2v-2026-06-12`, `wan2.6-i2v-flash`. Provider, region, model and price versions are explicit. Default remains disabled; demo assets remain visibly marked.
- 18 shots, 90 seconds, 8 person AI / 4 scene AI / 6 photo shots. At most 2 AI shots may become photos, with at least 6 person AI and 10 AI motion shots in a live standard export. Human A/B identity approval is still mandatory.
- Revoke/cancel/delete/content-change fences late outputs and removes product access immediately. Unknown dispatch is quarantined with its reservation retained. A known provider task is queried, never blindly resubmitted.
- Project budget 15,000 fen with 2,000 fen delivery reserve; account cap and idempotent ledger remain enforced for every paid operation, including story and frame creation. Report metered estimates separately from authoritative bills; do not pretend an API returns a monetary bill when it returns usage.
- Project file cap 2 GiB, assets retained 30 days, final exports at most 10 downloads. Bound preview traffic and rendering resources. Music must have an explicit license snapshot covering embedded customer delivery and wedding playback; no invented license or commercial sample.
- TDD for behavior changes. Run covering tests after each change, full backend/frontend checks after integration. Scan dependency advisories before commits; sync CodeGraph after each successful commit. Review before marking a task complete.

### Task 1: Real provider adapters and safe transport

Own `backend/app/wedding_providers.py` (new), provider request/output contracts and factory in `wedding_media.py`, provider configuration in `config.py`, `.env.example`, `backend/tests/test_wedding_providers.py` (new). Do not alter the generation worker or router yet.

Implement Ark chat JSON story output and Seedream multi-reference images through bounded HTTP requests. Wan R2V uses separate A/B single-person primary references plus the approved first frame; Wan I2V Flash explicitly requests `audio=false`. Both submit once with `X-DashScope-Async: enable`, expose a task-id checkpoint callback and a resume task-id input, then poll GET `/api/v1/tasks/{id}`. Output includes provider/model/task/request/usage/price metadata and integer cost in fen calculated from explicit price settings. Known unknown/running timeout keeps task identity and potentially charged cost; definite failures are distinguished from ambiguous network submission. Do not retry POST automatically.

Extend video request compatibly with primary references, existing task ID and checkpoint/heartbeat callbacks; defaults preserve current demo tests. Set connect/read/poll/total timeouts, byte limits, content checks. Generated URL downloads permit configured official HTTPS CDN domains only, reject userinfo/local/private IPs and redirects, stream with size limits and never forward API credentials. Do not log private URLs, stories, images or keys. Ark returns images as Base64 where possible. Validate JSON/image/MP4 envelopes and sanitize errors. Capability function reports unsupported providers/missing credentials separately from configured live adapters.

Tests use HTTPX MockTransport and synthetic bytes: exact regional payloads, A/B ordering, no unrelated refs, job resume without POST, failed/unknown tasks, timeout cost retention, malformed JSON/image/video, oversized responses, SSRF/redirect, disabled mode and metered cost rounding. Record official documentation links and payload decisions in the task report. RED then GREEN commits, focused tests and full backend suite.

### Task 2: Persistent story/frame production and video reconciliation

Own `backend/app/wedding_creation.py` (new focused service/router as appropriate), models/schemas/database migrations, `wedding_worker.py`, `routers/wedding.py`, `main.py`, demo story output in `wedding_media.py`, `backend/tests/test_wedding_creation.py` and worker regressions. Reuse Task 1 interfaces. The demo story provider may return a deterministic 18-shot template with explicitly unconfirmed source-derived facts, clearly labeled as demo, so the complete local pipeline is testable; it must not invent relationship events or claim AI generation.

Add durable separately identifiable story and frame jobs exposed by project endpoints: `POST /wedding-projects/{id}/story-jobs`, `POST .../frame-jobs`, `GET .../creation-jobs`. Owner requests include current project version and idempotency key. Story job snapshots source text, returns editable unapproved facts/18 shots. Frame jobs require approved story and produce all 12 AI shot frames, referencing only selected primary images for the relevant people. Photo shots use assigned originals. Explicit schema validation rejects invented fact references, wrong shot totals/types/durations, excessive subtitles, malformed person scopes and missing provenance. Story approval remains a human action.

Every call reserves project/account funds before dispatch, persists receipt/task metadata, checks live consent for the selected providers, and settles metered costs once. Account usage must survive deleting projects: current aggregation over live projects is insufficient. Keep durable account usage and minimal billing receipts independent of cascaded private project data; migrate/backfill current usage, never release possibly charged dispatch on deletion, and test delete/recreate budget bypass. Video worker supplies each person's primary photo separately. Establish `WeddingParticipant.primary_photo_asset_id` (nullable FK to WeddingAsset) and a shared scoped primary-reference loader now; Task 3 supplies selection UI/API and quality gates. Demo can use deterministic available own-person references, live calls require explicit primaries. Checkpoint provider task IDs before polling; recover known tasks by querying with fresh leases, and retain/quarantine unknown submissions. Persist usage and price/model/input/consent provenance for bill reconciliation. Add a local bill reconciliation command that accepts task-id/cost rows, adjusts settled estimates under a locked project/account, reports mismatch/over-cap without initiating calls; tests cover duplicate imports and unmatched rows.

Creation jobs use project-before-job locking, leases, bounded per-job dispatch, stale recovery, idempotency and cancellation/revocation/version fences. Snapshot the editable draft fingerprint at enqueue and compare it again before applying a story result; concurrent manual/autosave edits must not be overwritten even if project version is unchanged. A changed draft leaves an explicit needs-action result and retains billing provenance. No synchronous model calls in API requests. Update capability availability to reflect adapters. Worker polling handles transient DB errors with bounded backoff instead of dying. Keep generated data private and never expose provider URLs to clients.

Tests cover normal demo chain and mocked live results, malformed output, budgets, same-key replay, interrupted tasks, late output after revocation, unrelated portrait exclusion, lease races and bill reconciliation. Preserve existing API contracts where practical; report new endpoints and shapes for Task 5.

### Task 3: Photo readiness, licensed music and editable render history

Own a focused `wedding_authoring.py`/`wedding_photo_quality.py` service, necessary model/schema/config migrations, relevant wedding router functions, backend dependencies and `backend/tests/test_wedding_authoring.py`.

Photos get real decode, blur/face-size/single-person/occlusion warning signals with a local detector (OpenCV bundled cascade is acceptable; report limits and do not claim identity matching). Live production gates require each person at least 3 usable single-person photos, one explicitly selected primary reference and human confirmation of quality warnings; obvious corrupt/multiple-person images cannot become primary. Demo synthetic images must stay usable for plumbing without claiming portrait quality. Add per-person primary-photo API with scoped ownership. Primary change invalidates that person's dependent frames/candidates/approvals and render approval with explicit regeneration cost notice, while unrelated person assets remain reusable.

Add a configured JSON licensed music catalog with track IDs, paths, license/contract versions, validity for new projects, perpetual embedded-delivery permission and allowed uses. List safe metadata, project selection API and immutable per-render license snapshot; never serve raw music. An empty catalog is an explicit unavailable state. Legacy single file + license ID may provide a clearly labeled legacy track, but cannot imply rights beyond supplied metadata.

Allow rendering with subtitle overrides and selected music without regenerating video. Persist immutable ordered timeline snapshots (asset IDs, shot IDs, subtitles, durations, music snapshot) in RenderVersion. Editing creates a new render; source remains unchanged, final approval resets. `POST .../renders/{id}/restore` creates a new version from a still-valid same-project historical timeline; old invalidated/revoked/expired assets must reject. History response includes readable version metadata and review state. Cap subtitles to 2 lines, each 18 Chinese characters equivalent; preserve 90 seconds. All APIs owner/participant scopes remain enforced.

Tests cover min photo gates, blur/multiple-face detection (controlled detector fixtures), primary scope and invalidation, license expiry/rights errors, text/music-only rerender preserving candidate IDs and generation counts, immutable history, restore and expired/revoked rejection. Report contracts for Task 5.

### Task 4: Render recovery, actual media validation and retention quotas

Own render worker/FFmpeg code, focused `wedding_retention.py`, model/config migrations as needed, storage write/read quota hooks and `backend/tests/test_wedding_render_media.py` / `test_wedding_retention.py`.

RenderVersion gains persisted lease token/start/update/attempt/error metadata. Claim and completion are fenced; stale processing requeues safely, heartbeat while FFmpeg runs, bounded retries then needs-action/failed state. Parent-before-render lock order. No late attempt may overwrite newer outputs. Render uses immutable Task 3 timeline/music snapshot, validated same-project nonexpired assets and active consent before and after processing.

Add deterministic Ken Burns slow pan/zoom for photo inputs, exact segment trim/duration/fps and music-only audio (stereo AAC 48 kHz, target -16 LUFS and true peak <= -1 dBTP), burned Chinese ASS subtitles and demo watermark. Inspect real MP4 via ffprobe and full decode: H.264/yuv420p/30 fps, 1280x720 or 1920x1080, expected duration tolerance, valid audio when live music is configured. Fail closed on truncated/malformed results. Bounded time/memory/thread resources, no shell. Integration test generates a real short MP4 with synthetic photos and a locally synthesized test tone (explicitly test-only license), probes both sizes, checks changing image frames and audio; additionally render a synthetic 90-second 18-shot preview and verify full decode/duration. Tests may use a temporary portable FFmpeg/ffprobe dependency; document how provisioned.

Set expires_at for all uploads/generated/preview/export assets at 30 days. Worker runs periodic idempotent cleanup with bounded batches and retries; revoke access at expiry even if object deletion fails. Enforce 2 GiB aggregate private storage under project lock at every write, reserved capacity for in-flight output; enforce download cap 10 atomically and preview bandwidth ceiling (configurable default 20 GiB) without leaking raw URLs. Expiry cleanup removes dependent access/reviews safely and handles retained budget/provenance. Local/S3 parity tests, quota boundary/race and failed-delete retry tests. Deliver a runnable optional PostgreSQL/S3 integration harness; controller will provision/run available services and report any unavailable external environment.

### Task 5: Complete studio UI and remediate dependency advisories

Own `frontend/src/WeddingStudio.tsx` (split small focused wedding components/hooks allowed), `api.ts`, `types.ts`, studio styles/tests, frontend dependency manifests; backend dependency minima may also be updated after Task 3.

Add debounced autosave (1 second) with visible saving/saved/error state, persisted local draft recovery scoped by actor/project/version, flush before approve and guard against poll refresh overwriting dirty edits. Treat conflict as actionable, do not retry stale writes. Participant never saves owner story. Restore draft after reload/re-entry; no cross-project or cross-person leak.

Expose AI story and frame queue/actions/progress/error/retry, photo quality/3-photo readiness and main reference selection, licensed music chooser, postpreview subtitle editing, render history and restore selection. Media/view/review/export actions use the selected render ID, not `renders[0]`. Clearly distinguish demo/live/unavailable, budget waiting and unknown provider results. Labels, focus, mobile layout and private blob URL cleanup.

Resolve all npm advisories through compatible upgrades (use patched Vite/Vitest/JSdom/plugin versions); scan backend dependencies with pip-audit installed in a task-local environment, upgrade vulnerable runtime minima and test compatibility. Never suppress advisories or invent passes. Tests cover autosave debounce, reload, project switching, failed save/conflict, polling while editing, new workflows and render selection/approval/export scopes. Run frontend tests/build, backend full suite and dependency scans; record remaining justified environmental limitations.

### Task 6: Integrated acceptance, docs and final review

Controller owns runtime setup, optional real database/S3 tests, docs and evidence; a fresh reviewer reads the whole-branch package. Run backend and frontend suites/build, real FFmpeg acceptance and dependency scans. Exercise a complete no-paid-call product pipeline with synthetic media and licensed test-only music; separately verify provider wire contracts with mocked official responses. Run PostgreSQL lock/fencing and private S3 integration where a locally provisioned environment permits.

Update runtime instructions, `.env.example`, completion matrix, original spec status and plan progress so they distinguish code/tests completed from paid real-couple/model quality and actual commercial-music procurement. Add a gated acceptance runner for 10 authorized couples, costs/latency/pass rates and motion minimums; by default it must refuse paid calls without explicit execution flag and valid consent/materials. Do not execute that paid validation in this task. Invoke neat-freak at completion, sync docs/memory only from verified evidence, and retain branch locally.

## Progress recorded 2026-10-02

| Task | Frozen implementation | Independent gate |
| --- | --- | --- |
| 1 — providers and transport | `3487ac3` | Spec and quality PASS after fixes |
| 2 — durable creation/accounting | `6a9a8a5` | Spec and quality PASS after fixes |
| 3 — authoring/music/history | `4d654d6` | Spec and quality PASS after fixes |
| 4 — rendering/retention | `a01331f` | Spec and quality PASS after fixes |
| 5 — studio/dependencies/timezones | `63e161d` | Spec and quality PASS; actual browser paths verified |
| 6 — acceptance/docs/integration | CLI `ad5da1b`; 73 focused tests pass | Tool spec and quality PASS; whole-branch review pending |

Frozen backend baseline `eedbd83`: 457 passed/2 opt-in media skipped, native exit0. Frontend `63e161d`: 69 passed and production build passed; subsequent frontend source is unchanged. Actual synthetic 90-second rendering, public API workflow, private media browser use and isolated PostgreSQL/S3 checks are recorded with their precise source scope in the [completion audit](../../wedding-mv-completion-audit-2026-10-01.md). Overlapping focused tests are not added to full-suite totals.

Ten real couples, paid model generation, official invoice reconciliation, commercial music procurement and physical wedding-device playback remain external acceptance. No external mutation or paid call was executed by this plan's implementation; the branch stays local.
