# P01 Preview progress — 2026-09-19

P01 remains **IN_PROGRESS**; P02 remains locked. This checkpoint does not claim that the operational receivable gap is fixed.

## Tested candidate

- Draft PR #1091, code `801bb94d37b9b893d88edd462afd54af905ffa92`.
- Backend and built frontend ran from one clean checkout of that complete candidate, rather than application-file overlays.
- Preview authentication, local-database, worker and outbound-network isolation remain in an external private launcher. No Preview recovery tooling was added to the repository.
- Original workbook storage/download implementation: `7ffa25084d4be3e3cfcf5af3d946df063bbd5734`; UI tests: `801bb94d37b9b893d88edd462afd54af905ffa92`.
- 58 targeted Backend tests; 22 Frontend tests; full Vite build passed. This is not a claim of running every pytest test.

## Original attachment evidence

New uploads preserve the exact workbook bytes separately from public document projections. Reads resolve the current accounting actor, settlement owner, source document and immutable blob, then verify SHA256. Authenticated download returns the original XLSX. UI preview parses those same bytes and explicitly states its bounded display limits.

Actual Preview tests passed for the accountant and dedicated view-only account. Missing permissions return 403; another tenant returns 404; unauthenticated reads are denied. The accountant UI opened the workbook and its hash; the download button was exercised and endpoint byte equality was verified. Repeat upload created no additional file, draft or ledger entry. Older documents without original bytes return explicit unavailability; no replacement workbook is manufactured.

A clearly synthetic, unposted draft and dedicated access-test identities were added. Private before/after snapshots proved no pre-existing financial documents changed and no journals were created. Financial values, identities and fixture evidence remain private.

## Access interruption

While verifying the viewer UI after logout/login, both Preview API and independent code-server returned Cloudflare host 502. The project administration page reported that Preview was resuming; no paid chat message or publish action was used. A fresh API check still returned 502 at 2026-09-18 21:28:56 UTC. Re-login persistence is **BLOCKED**, not passed.

Before a later restart, recheck the production lease and shared reservations. This task's own reservation is `/tmp/mz2-p01-operational-preview.lock`, owner text `P01 operational 01a08c76-d160-73c1-bfb1-7ef6940e107a`. It could not be released after terminal access failed; do not clear another task's reservation. Last verified private candidate is under `/opt/mezan-p01-operational-20260919/repo`.

## Operational receivable gap

The current importer still does not create eligible receivable events. The existing bridge credits gross BNPL revenue, while the written tax contract requires a verified tax point. A specific owner decision was requested: source-invoice VAT separation, or retaining the gross bridge solely for isolated Preview testing with a production hold. No answer has been received at this checkpoint.

A local read-only qualification draft is not installed or claimed as an operational producer. Implementation of accountant preview/execute, common cross-ingress conflict/idempotency enforcement, partial/full-refund execution and UI-to-ledger-to-settlement acceptance remains required after the decision and restored access. Existing completed settlements must not be recognized, posted or reversed again.

## Integration/release boundary

All of #1082, #1083, #1085, #1086 and #1087 remain open/unmerged at this check. #1091 carries the compatible payout/fixture/conflicting-source changes while preserving newer refund handling. Do not merge the old parser wholesale; do not merge #1085/#1086 Preview recovery/pilot tooling into Production. Finish and review the integrated operational candidate before considering merge. Production deployment and release verification require separate authorization and remain unperformed.
