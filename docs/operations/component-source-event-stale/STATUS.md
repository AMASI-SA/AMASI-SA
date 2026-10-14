# Displayed review approval contract — Draft review checkpoint

Scope: independent Backend/Web fix with companion Android client contract. No production, release, accounting or opening-stock changes.

Base: b41cd9cc36a738a0528dd89ddc7349b31db2a361, production branch hotfix/prod-snap-meta-final. Task branch codex/component-source-event-stale. The commit containing this file identifies this checkpoint; no release intent is created.

## Proven mechanism

persist_component_source_snapshot increments revision for accepted persistence, including unchanged business facts. The previous completion claim compared a pre-transaction revision to the owner transaction revision. Real MongoDB 8.0.12 reproduction: six tests, four pass and two intended failures before the fix; the two false rejections involve metadata-only persistence and a same-facts webhook. Prior business snapshot fixes are ancestors of the base but leave this early guard in place.

## Implemented contract

Existing GET detail renders a read-only Mongo snapshot and returns a signed, 30-minute approval token. The digest binds business snapshot schema 2, workflow and resolved product identities; image/transport metadata is excluded. Existing POST carries that displayed token. The owner transaction validates its signature and both route execution inputs and transactional facts. Material changes still return component_source_event_stale. Completed retries retain existing idempotency. No new endpoint, store or synchronization mechanism.

Web no longer silently fetches a new approval at tap. Queue summaries route to the full detail view. Android companion branch codex/review-displayed-approval-token in AMASI-SA/amasi-mobile uses the displayed token and guards asynchronous confirmation/page changes.

## Verification checkpoint

- Isolated loopback MongoDB 8.0.12 replica set, disposable synthetic databases; external HTTP/Salla calls forbidden by fixtures.
- Source race suite after fix: 6 PASS, 0 FAIL/SKIP.
- Displayed approval suite: 11 PASS, 0 FAIL/SKIP (53.688s), including signature/context/expiry, source and recipe contention, ABA, snapshot GET and lost-response retry.
- Web: 5 suites / 35 tests PASS (Windows Node 24.19; pinned Linux toolchain still required).
- Android: type checking and repository contract checks PASS; device E2E not performed.
- First broad backend run: 55 PASS, 26 FAIL because assembly fixtures omitted the newly required token. Fixture updated; full regression rerun pending.

## Limits / next safe action

Specific incident writer and before/after production snapshots remain UNKNOWN. The reported time is 01:08 Riyadh; date and production logs are not independently verified. No real order replay or Salla test request occurred.

An isolated concurrent first creation of the config-fence collection returned Mongo 112/HTTP 500; the existing-row contention test passes. Catalog-only writes outside existing acceptance fencing after a transaction snapshot begins are not proven to force rejection; the transaction must never adopt unapproved newer facts.

Old clients cannot create new approvals without the token (409 review_approval_required). New clients against an old backend stop locally; coordinated client availability is required. No permissive fallback.

Drafts: AMASI-SA/AMASI-SA#1330; companion AMASI-SA/amasi-mobile#268 at 8a1b9c8a4953a3ae2fee3a86fbd1652197297061 (tree cb193ebd6d0d9357643fccf403dbf712f3e20091; base f2d7ed60fb569dd226c6b60e16538064c368f822).

Independent security review found no blocker to opening Draft; route-basis ABA comparison is present. Full RCA, writer paths, compatibility, test matrix, timing and risks: RCA.md. Current timing evidence: current-approval-timing.json (20 successful first approvals; no paired performance baseline).

First backend CI at 82003503792793e1731f2db9865d389630fbf09a: Review Completion 316 PASS/1 FAIL, G47 140 PASS/1 FAIL, both the same preexisting stale-source test missing its newly required approval token. That test now obtains a valid displayed token and asserts a real material source change leaves plans/units/stock exactly unchanged; focused test PASS. Guards were not weakened. Security Gate, CodeQL, Linux frontend build and six other workflow checks passed at that checkpoint; new exact-head CI remains required after this test adaptation.

Android exact-head CI: TypeScript and new approval contract passed, but existing supplier-invoice-service-policy check failed. Running its exact committed blobs against both base and candidate reproduces the identical service-selection assertion. This unrelated failure is not changed here. Preview/production OTA, APK/AAB and Expo-authentication steps were SKIPPED; nothing was published. Physical-device E2E remains unverified.

Next: inspect final backend exact-head CI and full local regression output, record exact HEAD/TREE in Issue1006 and final report. Proposed combined acceptance remains BLOCKED while Android CI has the existing failure and device verification/client rollout compatibility remain unresolved. This is a reviewable Draft, not merge/deployment approval. Production changed: no. Production business writes: 0.
