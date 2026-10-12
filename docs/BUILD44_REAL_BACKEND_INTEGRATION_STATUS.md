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

Local verification: diff whitespace and Python syntax PASS. Added existing
suites collect 532 cases without network. Combined collection is incomplete
locally because webauthn is absent; its exact dependency is already installed
by CI. Full execution on this integrated tree is pending, not PASS.

Release blockers still open:

- Old review clients do not supply the displayed approval token; the new
  completion service requires it. Do not remove the approval/session fences
  or claim backward-compatible writes until a tested transition exists.
- Current Production shipping behavior is preserved, not newly certified.
  #1335 is not assumed deployed or silently imported. Provider remote races
  and the operational limits of GET-only reconciliation remain explicit.
- Trusted EAS is installed closed, not commissioned. Live Expo admission,
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
external POSTs. Old-client compatibility must be proven before either rollout
or rollback is authorized.
