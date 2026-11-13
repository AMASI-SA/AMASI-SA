# READY-RECEIVED-PIECE-20261113 — Verified source acceptance

**Task:** Restore the physical supplier-received piece Ready gate in mixed reviewed orders without bypassing source status, employee custody, services, hold, component generation, source snapshot or stock consumption safeguards.

## Proven user symptom (private source)
An authorized **read-only** production Assembly search response for a reported order showed Salla in_progress, Mezan workflow reviewed, a physical supplier item ready_for_assembly after confirmed supplier and employee receipt, zero remaining services, but can_mark_ready=false / assembly_order_not_ready, while direct/operational cards were eligible. No customer names, raw JSON or order identifiers are included in repository evidence.

## Reviewed source
- Repository: `AMASI-SA/AMASI-SA`
- Production base: `b64121c265d8d6e158df0d9fb88debc52c95317d` (`hotfix/prod-snap-meta-final`)
- Immutable reviewed source commit: `9fee416d40e2b10031fb3d80c532d94e7593f5c9` on `codex/ready-received-piece-20261113`
- Draft PR: https://github.com/AMASI-SA/AMASI-SA/pull/1904 (candidate source is not merged/published)
- Files changed from reviewed Production base: `backend/review_local_policy.py`, `backend/preparation_piece_operations.py`, `backend/tests/test_review_local_assembly.py`, `backend/tests/test_g47_component_lifecycle_integration.py`, original WIP `docs/operations/READY-RECEIVED-PIECE-20261113/STATUS.md`.
- This acceptance evidence is intentionally on its own branch, to **preserve** the exact successful application source/CI SHA (changing the original task STATUS in the source PR would invalidate that SHA's test identity).

## Behavior and safety
- Source read and physical Ready write now use a narrowly scoped reviewed-stage exception for proven two-sided supplier/employee receipt; direct/operational semantics are not loosened.
- Verified custody requires supplier receipt, employee custody receipt, non-held piece, zero incomplete services and actual ready_for_assembly.
- Local and accepted historical component-plan paths both enforce current Salla in_progress, current component generation, accepted review proof and a current numeric source revision matching the lifecycle proof (missing/advanced revision fails closed).
- Search checks these constraints without mutating component lifecycle.
- Physical Ready transition advances the Mezan workflow reviewed → in_progress within the same owner transaction as component consumption, Ready state and audit event, protected by revision fence and idempotent retry.
- Historical full order shipment additionally requires exact provider source units == reserved units, all component units consumed, fully assembled physical/direct/operational work, current revision and full address. Thin or incomplete sources cannot produce shipments.
- No changes to Android binaries, external providers, ads or accounting were made.

## Exact-head GitHub CI — all completed successfully
Commit `9fee416d40e2b10031fb3d80c532d94e7593f5c9`: **18/18 workflow runs concluded success; no failed/cancelled/pending runs**. Key independent isolated Mongo evidence:
- Review Completion Resumable: run `43452642418` — 312 Android operation recovery tests + 59 subtests PASS; 669 broad review/approval/provider regression cases + 345 subtests PASS; 1 independent acceptance guard PASS; no skipped/error/failure cases.
- G47 Focused Integration: run `43452641388` — 329 real-Mongo integration cases + 127 subtests PASS; 436 additional order-cost cases PASS.
- Operational Stock Eligibility: run `43452641222` — 3576 cases + 7 subtests PASS.
- Production Preparation Piece Operations: run `43452641019`, backend contract and frontend test/build both PASS.
- Mezan Release Readiness: run `43452641198` PASS for candidate source A; clean-clone v5 B adapter rehearsal and manual-deploy job were conditionally skipped, as this task is not a reviewed B release.
- Security Gate, CodeQL, supplier service policy, Assembly Direct Preparation Receipt, fulfillment, print, accounting, and other triggered workflows all concluded success on the same SHA.

Prior checkpoints `7641a7d...`, `32d9b7f...`, and `c4200dd...` had incomplete/failed test states and are superseded. Their failures were inspected and addressed: old query-shape fixture, fixture read permission, missing source revision fencing and unsupported expectation of shipping on a thin legacy source.

## Remaining/limitations
- The reported unpushed local Codex patch is not available; this branch re-created the repair from the authenticated read-only response and actual Production code, not a byte-identical patch review.
- The authorized production GET does not expose `completion_mode` or actual component-plan generation/revision, so this acceptance proves both supported code paths under synthetic fixtures, not eligibility of the live order's current private component plan.
- Android version 77/1.0.62 is not rebuilt; code change is on the server.
- Exact live-device Ready activation and an actual live-order write have not been attempted.
- No v5 release intent B has been generated, no Release Guard lease, no merge and no production deploy. **Production changed by this task: no.**

## Next safe action
1. Independent reviewer inspects PR #1904 diff and 18/18 exact-head checks. Confirm the private order has a current accepted plan/snapshot; never bypass if missing.
2. If owner explicitly authorizes merging/releasing, use only protocol v5 A/B reviewed intent and Emergent guard status/prepare/prepublish/publish/verify 3×. Do not squash/rebase source A after freeze.
3. After a governed publish, read back the live assembly search and verify Ready eligibility for the specific received piece; do not mutate a completed piece just to test. Repeated Ready must not deduct materials again or create duplicate shipment.
4. The repository-wide Issue #1006 continuation ledger is at its 2500-comment limit; preserve this task document and PR body as a durable supplement.

**Evidence-only commit:** this file is a standalone non-runtime checkpoint; do not merge it instead of the tested PR source.
