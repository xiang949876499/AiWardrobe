# AiWardrobe — wedding branch guidance

Keep wardrobe functionality intact; wedding projects use independent participants and private media.
Runtime/contracts: `docs/wedding-mv.md`; product requirements: `docs/superpowers/specs/2026-10-01-ai-wedding-mv-design.md`.
Completion evidence and external limits: `docs/wedding-mv-completion-audit-2026-10-01.md`.

## Wedding production boundaries

Run `cd backend; python -m app.migrations upgrade` before production API/worker; startup verifies only. Compose uses a successful migration service barrier. Invitation acceptance requires preview-bound disclosure hash; re-entry rotates session without new consent. Use explicit storyboard revisions for approved facts/scenes, preserving unchanged lineage/attempts. Shared frame/composition guards apply to LIVE even during tests; unknown provider outcomes retain reservations. See `docs/wedding-mv.md` for API contracts.

## Commands
- Backend: `cd backend; python -m pip install -e ".[dev]"`; `python -m pytest`.
- API: `python -m uvicorn app.main:app --host 127.0.0.1 --port 8031`.
- Independent worker with identical environment: `python -m app.wedding_worker --poll-seconds 2`.
- Frontend: Node >=24.14.0 <25; `cd frontend; npm ci`; `npm test`; `npm run build`.
- Compose >=2.24: frontend5173/API8000; local Vite5174/API8031.

## Invariants
- Lock wedding project before child jobs/renders; perform provider, storage and FFmpeg I/O outside DB transactions.
- Validate current consent, version, lease and exact asset/person reviews before publishing late results.
- Resume known provider IDs by GET. Quarantine ambiguous submissions; never automatically repeat paid POST.
- Preserve immutable render/music snapshots and account financial receipts across project deletion.
- Scope browser draft/session/blob state to account, actor, project and content version; raw If-Match protects same-version writes.
- Private access rechecks revocation/expiry; storage quotas include staged and physically retained bytes.
- SQLite is for development/tests; concurrency acceptance and production use PostgreSQL.
- Default wedding mode is disabled; demo stays visibly marked and cannot establish identity/music/commercial acceptance.
- Run meaningful covering tests and dependency audits before commits; after every code commit run `codegraph sync` (or `codegraph init -i`).
- Keep external actions read-only absent explicit authorization. At task completion apply neat-freak and reconcile documentation with verified evidence.
