# Build44 real Backend integration — WIP, not release approval

Production base: `d3f3f54f56ae275411262a4cad18cdcc4fcee31b` (#1339).
Refreshed after the CI audit; its owner TikTok reply settings and release intent
are preserved. Source-only replay applied without conflicts. Earlier CI and
failure inheritance comparisons below used `bccb8da96938cd6453fd535c8e9a2d2ebc69b273`.
Originally composed on `12ec36c7ca9b43c694437fcff5d48fc0141254eb`, then
incorporated the newer Production #1337 source and intent without conflict.
Draft PR: https://github.com/AMASI-SA/AMASI-SA/pull/1338.
Task branch: `codex/build44-real-backend-integration`.
Android source remains PR #270, `ab79138abe6a42b71493bab7e4ad19db2809fa81`.

Source-only replay: Review Readback V2 `e6fc7d04f9a3bcaac9b2889a0837b3728ceb534a`
and the paired supplier receiving/attempt modules plus tests and contract checks
from #1329 `c2ae3f6a1a55dedfdca08371093403430217c4e3`. Both apply cleanly.
No release-intent or deployment guard change. No Offline/Mock changes.

The isolated acceptance workflow retains all 15 review/auth suites and adds
20 supplier/invoice/refresh/preparation/shipping suites: minimum 823 pytest
cases, including one wrapper for five existing real-Mongo overlap scenarios.
Linux network-none Mongo 8.0.12, no published ports, cleared test environment,
fail-on-skip, and source/evidence identity checks are retained.

First isolated run `38272101554` (source `5827b0b553f28d2399f505ffdc100c3a2a4082be`):
822 PASS, 1 FAIL, 0 SKIP, 114 passing subtests; Web 21 PASS.
Artifact `11757671329` retains the failure. The explicit overlap fixture
inserted a second canonical order after accept() had already seeded it;
find_one returned the original under_review order. The fixture now uses the
real synthetic webhook writer, asserts one canonical identity and still
requires in_progress and every original stock/rollback assertion. No business
guard or expected outcome was relaxed. See the successful final execution below.
Run `38273105414` also retained 822 PASS / 1 FAIL / 0 SKIP: the real webhook
correctly rejected a material change from the fixture's minimal DTO source
to the full order. Acceptance now starts from that same complete canonical
synthetic webhook/DTO; the status-only update must preserve its reservation.
Local focused verification then passed all five overlap scenarios on a fresh
Mongo 8.0.12 replica at 127.0.0.1:27644 with a cleared environment and Python
socket audit denying other connections (not an OS network namespace).
Fixtures now use canonical mapped item IDs. The supplier rollback scenario
models legacy writer selection followed by an actual synthetic owner-state
change to v2 before the transaction: current Production normally routes v2
owners to the native writer. The original 423 guard and all rollback/zero
ledger assertions remain. No accounting runtime change was made. Final Linux
network-none acceptance subsequently passed (evidence below).

Release blockers still open:

- Old review clients do not supply the displayed approval token. Their 409
  now explicitly requires an approved client upgrade or current Web review
  at /order-review, with retry_post=false and no misleading refresh loop.
  The HTTP test retains its unchanged-state assertion and checks this
  transition. No token is minted on their behalf and no guard is bypassed.
  This preserves operations through Web during Android migration, not legacy
  Android review writes. Web access/permissions and employee acceptance of
  that temporary workflow must be verified before rollout.
- Current Production shipping behavior is preserved, not newly certified.
  #1335 is not assumed deployed or silently imported. Provider remote races
  and the operational limits of GET-only reconciliation remain explicit.
- Trusted EAS is installed closed, not commissioned. Official EAS CLI 24.12.1
  is installed in the existing Codespace (no build executed). Browser login
  has reached Expo's eas-cli approval page for the owner to complete.
  Live Expo admission,
  approved signing identity and trusted launcher bindings remain required.
- Samsung/printer field verification requires a separately authorized test
  account/order plan and, later, one separately authorized APK build.

CI blocker repair (2026-10-12):

- Accepted isolated run 38274835625 on 210602786feed14b95a0530a88c26ceac3eec670:
  823 PASS, 0 FAIL, 0 ERROR, 0 SKIP; Web 21 PASS. Artifact 11759027534,
  evidence.zip SHA256 1e656589ab9377a333f208776bcd26b6ba8be7422ba1c1ce1a5b283af1f2dad7.
  Mandatory downloaded-evidence/source/run readback passed. This is evidence
  for that exact source, not an assertion about a later HEAD.
- Review Completion Resumable 38274844345: 37 failures were missing signing
  configuration in the assembly/catalog fixture. The isolated acceptance
  runner supplied a synthetic key; the standalone workflow did not. Scope
  the same public test-only key to each assembly fixture with cleanup; retain
  real issuance/verification and add a missing-key 503 regression. No runtime
  signing fallback or real secret has been added.
- Runtime Stability 38274844088: 59 PASS / 1 FAIL. Session-owner resolution
  newly reaches the existing PDF import chain. Install its declared PDF
  dependencies in the focused CI job; production requirements already contain
  them. This is a newly exercised import, not a proven inherited failure.
- Release Readiness 38274844245: the integration merge touched intent in full
  history although intent bytes matched Production. Replay the source-only
  commits directly above bccb8da; no runtime or intent bytes changed. Preserve
  old 21060278 on codex/build44-ci-before-history-fix-21060278. Do not bypass
  the source-A gate or create a new release intent.
- Advertising 38274843756: missing cryptography in focused CI. Its workflow
  and import chain are unchanged from Production; add the existing dependency
  pin to CI only. Inherited configuration, not an observed baseline test run.
- Accounting 38274844298: concurrent advance captures return 409
  advance_existing_capture_or_tax_requires_reconciliation rather than 200.
  The workflow, test, and relevant runtime files are byte-identical to bccb8da.
  Inherited source/test path; a Production-base execution has not yet proved
  identical failure. No accounting assertion or reconciliation guard changed.

Next: verify the repaired failed/affected jobs. Do not manually rerun the
already accepted 823-case suite; repository-required automatic PR checks may
still trigger on push. Ready/printing and the old-client Web transition above
remain release blockers and are not resolved by green dependency checks.

Production changed: no. No Merge, Deploy, Restart, Release Intent, provider
business calls, EAS Build, APK or OTA performed by this integration.

Later rollout: independently approve tested source, then prepare the governed
intent and Backend release under the applicable release protocol. Verify
runtime identity before the separately approved Android build/distribution.
Rollback must restore the reviewed prior Backend source/intent through that
same protocol, retain scan idempotency history, and never replay uncertain
external POSTs. Before any Backend rollback, stop accepting new supplier
scans/closures and reconcile every pending or uncertain attempt; an older
writer that does not honor the new lease cannot safely run over active
attempts. No deletion of recovery history is permitted. Old-client
compatibility must be proven before either rollout or rollback is authorized.
