# Build44 real Backend integration — WIP, not release approval

Production base: `bccb8da96938cd6453fd535c8e9a2d2ebc69b273`.
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
guard or expected outcome was relaxed. Corrected final-tree execution pending.
Run `38273105414` also retained 822 PASS / 1 FAIL / 0 SKIP: the real webhook
correctly rejected a material change from the fixture's minimal DTO source
to the full order. Acceptance now starts from that same complete canonical
synthetic webhook/DTO; the status-only update must preserve its reservation.

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

Next: run the exact integrated acceptance workflow, resolve any failures,
then close legacy-client transition and shipping contracts before release
approval. Preserve accepted unchanged Android CI evidence.

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
