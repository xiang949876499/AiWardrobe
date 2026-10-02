# Gated ten-couple acceptance

`backend/scripts/wedding_acceptance.py` is an operator-driven application API client.
The checked-in manifest contains placeholders and false attestations; the evidence
file is empty. They prove no consent, music rights, model quality, invoice or result.
Default offline output is **INCOMPLETE / NOT_EXECUTED**, including for a valid prepared
manifest. No paid or real-couple execution was performed when implementing this tool.

Use Python 3.11+; the CLI itself uses only the standard library. Commands below run
from the repository root. Keep actual manifests, evidence and checkpoints outside Git.
Never put credentials, names, stories, photos or URLs in these files. IDs must contain
only letters, digits, underscores or hyphens. Tokens are read only from explicitly
named environment variables and are never printed or saved. Supply owner sessions
through your existing secure environment workflow; do not paste token values into
shell commands or checked-in files.

## Prepare and inspect

```powershell
python backend/scripts/wedding_acceptance.py --manifest docs/examples/wedding-acceptance-manifest.json --evidence docs/examples/wedding-acceptance-evidence.json
python backend/scripts/wedding_acceptance.py --manifest acceptance/private-manifest.json --stage preflight --base-url http://127.0.0.1:8031
```

The first command is entirely offline, including without tokens. All network stages
require exactly ten distinct project IDs, current versions and four distinct current
person-shot IDs per project. The categories are `a_closeup` (A only), `both` (A+B),
`side_turn` (one or both people), and `holding_hands` (A+B). The operator must verify
the intended shot semantics and low-motion prompts in the application; role metadata
alone cannot prove that a shot depicts a turn or holding hands.

Set the four manifest attestations to true only after independently verifying both
adults' authorization, current provider scopes, own-photo readiness and the actual
commercial music grant. Server preflight independently requires active A/B consent,
three usable photos and an explicit primary per person, live fixed-baseline provider
availability, an approved 18-shot/90-second storyboard and valid catalog rights.
Known scene frames with A/B portrait scope are rejected. Human visual inspection is
still required for undetectable portraits within otherwise unscoped scene content.
Public capabilities do not expose model IDs or full consent scopes: the app's pinned
capability and quote checks remain authoritative gates; these values are not invented.

Preflight emits GET requests only and does not create quotes. It performs one bounded
pass over the ten projects, with no polling, media downloads or reviews. HTTPS or
exact `localhost`, `127.0.0.1`, `::1` HTTP is required. Base URLs cannot include a path,
credentials, query or fragment. Redirects and proxies are disabled. Each HTTP request
runs in a disposable, isolated stdlib subprocess (hidden on Windows). A parent-owned
15-second monotonic deadline covers process-start overhead, control delivery, DNS,
all address connection attempts, TLS, headers, body and result transfer. Expired startup
never receives request control. Deadline expiry kills the process and confirms its
exit before returning `RESPONSE_TIMEOUT`; there is no background HTTP thread that can
later send a POST. OS cleanup has a separate two-second allowance. Kernel process
creation and scheduling are OS operations, so this is not a hard real-time guarantee;
a startup overrun is rejected before dispatch. If OS termination/pipe cleanup cannot
be confirmed, the distinct `TRANSPORT_CLEANUP_UNCONFIRMED` stop requires operator
investigation and must never be treated as permission to resubmit.

Private request control travels through stdin, not arguments or temporary files.
Control input and HTTP response bodies are capped at 2 MiB; result IPC is capped at
4 MiB + 1 KiB. Bounded pipe-reader/writer threads prevent Windows synchronous stdin
writes from defeating the deadline; these threads cannot issue HTTP. Workers do not
spawn descendants, and child stderr is discarded. Errors, including malformed HTTP
status/header exceptions, use fixed reason codes without server text or tracebacks.

## Explicit staged dispatch

These commands are templates for separately authorized future paid acceptance.
Replace the project and file names. The runner never approves, selects, edits consents,
accepts fallback, or triggers a final download. Complete those human actions in the UI.

```powershell
# Submit remaining baseline video shots once, then exit.
python backend/scripts/wedding_acceptance.py --manifest acceptance/private-manifest.json --stage generate --project-id PROJECT_ID --checkpoint acceptance/baseline.json --execute-paid
# GET the known job once; repeat this command manually for progress.
python backend/scripts/wedding_acceptance.py --manifest acceptance/private-manifest.json --stage resume --project-id PROJECT_ID --checkpoint acceptance/baseline.json
# An explicit new trial for one category, after the prior job is terminal.
python backend/scripts/wedding_acceptance.py --manifest acceptance/private-manifest.json --stage generate --category both --project-id PROJECT_ID --checkpoint acceptance/both-trial-2.json --receipt acceptance/baseline.json --execute-paid
# After human candidate selection and exact asset/person approvals in the application.
python backend/scripts/wedding_acceptance.py --manifest acceptance/private-manifest.json --stage render --project-id PROJECT_ID --checkpoint acceptance/render.json --execute-paid
```

Every generation uses the app's noncharged GET quote followed by its durable queue or
regenerate POST with the same recorded quote/version/idempotency key. Server budget,
account cap, provider quarantine, readiness and consent gates stay in force. Render
uses the durable app render endpoint, with the selected catalog track. The execution
flag is required for both generation and render. Passing it to offline, preflight,
resume or report does not enable mutations in those stages.

Each operation has its own checkpoint. A checkpoint lock prevents concurrent reuse;
JSON is written to a flushed temporary file and atomically replaced **before POST**.
Existing checkpoint paths cannot dispatch again. A killed process can leave a lock;
inspect the checkpoint and server operation before manually removing that lock. Never
delete an ambiguous checkpoint to try again with another key.

A lost response, HTTP failure after dispatch or crash in `submitting` means STOP.
Neither generation nor render is automatically retried. After independent operator
reconciliation, `--stage resume --resolved-id KNOWN_ID` can attach an ID: project,
version and scope must match. For generation, actual candidates bound to that job must
prove the exact recorded shot set; a queued job without candidates stays stopped.
For render, the project-bound GET, version, exact shot/asset map and music track must
match. Known successful POST receipts resume using GET, never another POST.

Candidate attempts are capped at three per target. Repeat `--receipt PATH` to supply
known checkpoints for prior operations. Completed jobs whose complete target sets are
proved by candidate bindings, and known receipt target sets, exclude unrelated shots
from this cap. Public jobs omit requested shot IDs: failed/partial historical jobs
without scope proof conservatively count as possible attempts on every target. This
can stop trials early with `UNPROVEN_PRIOR_JOB_SCOPE_CEILING`; it does not justify a
fresh key. Keep all checkpoints. Simultaneous operators and manual external generation
must be suspended during acceptance: the runner cannot atomically reserve an experiment
attempt counter across independent clients.
Report attempt counts are conservative upper bounds from visible candidates and all
current-version jobs; unscoped failed attempts do not disappear from the metrics.
Report uses this server evidence conservatively even when dispatch used local receipts.

## Independent human and invoice evidence

`wedding-acceptance-evidence.json` is an array, initially empty. Add one record per
project only when measured results exist. Allowed fields:

| Field | Evidence required |
|---|---|
| `project_id`, `version`, `render_id` | Exact current project and chosen render identifiers |
| `rating` | Integer 1–5 supplied by the couple |
| `willing` | Boolean: explicit willingness to play it at the wedding |
| `role_facts_correct` | Boolean: independently checked names, roles and facts |
| `commercial_music_rights` | Boolean: checked actual order license and delivery/playback rights |
| `motion` | Four category objects, each `{ "passed": boolean, "candidate_id": "ID" }`; true records must bind the currently selected exact candidate. A failed category without any candidate may omit `candidate_id`. |
| `bill` | `authoritative: true`, `complete: true`, opaque `reference_id`, and nonnegative integer `model_fen`, `music_fen`, `render_fen`, `storage_fen`, `other_fen`; include all variable costs. Zero must be a measured/accounted zero. |
| `timing` | Zoned ISO `start` and `end`, plus explicit `human_intervals` array of `{ "start": "...", "end": "..." }`; empty means measured no human pauses. |

Timing starts **after materials, story and static frames are confirmed** and ends when
the preview is available, under normal queue conditions. Human confirmation intervals
are subtracted; overlapping, negative, unzoned or out-of-range intervals are rejected.
Missing measurements remain missing. The runner does not infer missing timestamps from
job update fields. Authoritative invoice evidence is operator supplied and must be
reconciled externally; provider usage/metered amounts are labeled estimates, never
certified total bills. Supporting private invoices and human records stay in your
controlled evidence system, outside this manifest/report.

```powershell
python backend/scripts/wedding_acceptance.py --manifest acceptance/private-manifest.json --stage report --evidence acceptance/human-and-invoice.json --output acceptance/summary.json
```

Report uses metadata GETs, not the final media download that consumes a download slot.
It checks actual selected candidate provider/model/video MIME, asset expiry and exact
per-person approvals. A publicly owner-selected candidate may retain `review_result: pending`; the runner uses selection plus exact participant approvals, and still rejects `rejected`, wrong provider/model, missing MP4, demo or expired assets. It does not require an unreachable aggregate `approved` flag. The chosen current-version render must match current shot assets,
have 90,000 ms output, owner approval, exact A/B preview reviews and an unexpired final
video asset. Fallback is counted only when explicitly accepted and its selected photo
has the required current per-person review. The metric uses actual media types, because
the public fallback shot retains its original AI shot type.

The fixed denominator is ten projects. Default low-motion pass is at least 18/20
(`a_closeup` and `both`); `side_turn` and `holding_hands` are separately reported over
20 stress samples and never inflate that denominator. At least eight qualified 90s
films must have complete official variable costs <=15,000 fen; at least six successful
films must retain 8 person AI/4 scene AI/6 photos. Other qualified films may have at
most two explicitly accepted photo fallbacks, at least six person AI and ten total AI
motion shots. At least eight ratings must be >=4 with willingness true, and all ten
must have correct facts and actual music rights. Missing bills, ratings, measured time,
render or current-candidate human evidence gives INCOMPLETE. A complete dataset below
the thresholds gives FAIL. P50 <=30/P95 <=90 minutes are separate goals, not SLA or
quality pass gates; P50 is the median, P95 is nearest-rank (maximum for ten samples).

The alternative-provider comparison remains **external manual evaluation** using the
same authorized materials/prompts and separately approved budget. No alternative adapter
exists in this runner. Engineering tests and synthetic pipelines cannot certify those
external model, likeness, invoice, music-procurement or human-satisfaction results.

CLI exits 0 when the requested bounded stage was processed (including INCOMPLETE/FAIL
reports), and 2 when safely stopped. Automation must inspect JSON status as well as the
exit code. Do not edit or copy checkpoints between origins/projects/versions. No files
contain raw API responses; reports expose only IDs, bounded metric fields and known
provider/model labels. Recognized server failure codes are retained; other provider
errors become `UNCLASSIFIED_SERVER_FAILURE` without copying private error text.
