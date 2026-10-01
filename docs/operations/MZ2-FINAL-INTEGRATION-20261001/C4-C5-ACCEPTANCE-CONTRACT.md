# C4/C5 acceptance-contract audit — read-only

**Superseding user decision: Acceptance-only Smoke B is now explicitly approved.**
The complete real-HTTP scenario below will execute on the isolated current
application/replica-set with real authentication, exact source HEAD/TREE,
canonical pause before/after, nonexistent-session GET404, opening-draft POST423,
and full trusted database fingerprints. The reusable harness is
`scripts/testing/mz2_smoke_b_acceptance`. It runs only after C3 completion.
Its result is Acceptance evidence only; it cannot change production_verified,
live readiness, opening, activation, write-control or Release Guard. The older
Production-only acceptance conclusion below is historical for that separate
Production hold. This decision does not convert incomplete 16-stage evidence
into full business UAT acceptance. Execution results must be recorded separately.

**Conclusion:** the current local Acceptance app + replica Mongo is admissible for separately labelled non-production source/UI acceptance. It is **not established as a substitute for the recorded Production Smoke B gate**, and cannot make the current readiness response report PASS. C4 remains an environment/acceptance-contract blocker; C5 remains incomplete business acceptance. No code, guard, acceptance definition, service, production database, or financial action was changed or executed in this audit.

Important correction: **an existing safe Smoke B HTTP probe path does exist**. The missing pieces must not be described as absence of every executable probe. Repository search found no dedicated end-to-end Smoke B runner or PASS-proof consumer, which is a different limitation.

## C4: what is actually delivered

The [recorded safe probe decision](https://github.com/AMASI-SA/AMASI-SA/issues/1006#issuecomment-5903295449), reaffirmed [after runtime verification](https://github.com/AMASI-SA/AMASI-SA/issues/1006#issuecomment-5909748419), specifies:

1. Verify the exact deployed runtime/release identity and read canonical paused state under the intended authenticated owner.
2. Take complete trusted owner-scoped before fingerprints, not collection counts alone.
3. Generate a random session ID and confirm it does not exist with a GET returning404.
4. Submit a valid `POST /api/accounting-module/onboarding/sessions/{nonexistent-id}/opening-draft` action. Expect423 with `mz2_writes_paused` before callback work. If the barrier were unexpectedly open, its first session load fails404 and aborts before financial work; non423 is a failed probe, not permission to continue.
5. Take equivalent after fingerprints and require exact equality. Do not change write-control, create a session for the probe, post opening, activate, replay or backfill.

Current source still supplies that path:

- `backend/accounting_onboarding.py:390-417`: authenticated handoff calls `atomic_owner`; callback begins with `_load` at394.
- `_load`, same file50-53: exact owner/session query, missing session404.
- `backend/accounting_atomic.py:116-129`: owner transaction lock, fresh paused check,423 before callback. Abort rolls back the transactional owner revision increment.
- `backend/accounting_write_control.py:94-102`: canonical authenticated GET state.
- `backend/tests/test_accounting_onboarding.py:120-134`: paused/missing-control handoff denial and unchanged fingerprints; these are isolated test evidence, not an executed relevant-environment Smoke B.

The [earlier accepted boundary](https://github.com/AMASI-SA/AMASI-SA/issues/1006#issuecomment-5899460953) requires Smoke B or equivalent trusted **Production** proof before live financial enablement. The [subsequent actual environment investigation](https://github.com/AMASI-SA/AMASI-SA/issues/1006#issuecomment-5903965192) distinguishes the official Production viewer from Preview and requires a supported owner-authenticated POST executor plus complete trusted Production snapshots. It explicitly rejects counts alone. This supersedes the still-earlier claim that no safe HTTP probe existed, but does not approve Preview equivalence.

The [frozen PR1236](https://github.com/AMASI-SA/AMASI-SA/pull/1236), head `9e884f697c03e52bb98513f1cea141b0d31261aa`, remains Draft/open/unmerged in the fetched metadata and points to the [C4/C5 stop record](https://github.com/AMASI-SA/AMASI-SA/issues/1006#issuecomment-5939105239). The latest [C1 continuation](https://github.com/AMASI-SA/AMASI-SA/issues/1006#issuecomment-5939758794) records the user's conditional Acceptance/Preview allowance **only if the actual contract supports it**. No fetched decision defines equivalence or retires the Production-specific gate.

Independently, `backend/accounting_onboarding.py:237-254` unconditionally emits `smoke_b_production_proof_required`, owner authorization required, production_verified=false, ready_for_live_post=false, stage_16_locked=true, Smoke B BLOCKED_BY_ENVIRONMENT, and activation false. [Track A API contract94](https://github.com/AMASI-SA/AMASI-SA/blob/f3be643522d8d3709a0508a9d3aba96fdbab727a/docs/operations/MZ2-TRACK-A-CUTOVER-20260930/API_CONTRACT.md#L94) explicitly supplies no mechanism to mark Smoke B passed. There is no environment parameter, persisted attestation read, or approved success transition here.

**Safe next decision:** either supply the already-required trusted Production execution/snapshot authority under separate authorization, or explicitly define and approve an Acceptance-specific smoke contract and how it relates to the still-separate Production hold. Do not implement a PASS toggle or relabel local evidence to close the existing gate. The safe probe could be executed against the authorized local environment as clearly labelled acceptance evidence, but this audit did not execute it, and that result alone would not satisfy the recorded Production condition.

## C5: what can and cannot close locally

Delivered executable subsets:

- `scripts/testing/mz2_onboarding_ab/browser.cjs`:23 browser/API scenarios; metadata save/readback, exact identities, zero/N/A, CAS/replay, section meanings, preview/review lock,16-stage navigation, mobile layout and unchanged non-setup fingerprints. Lines235-243 assert blocked live gates and explicitly retain business_uat=BLOCKED.
- `scripts/testing/mz2_inventory_d/browser.cjs`: Stage10 product/component metadata and planned valuation persistence; not physical stock approval.
- New C1 `browser-c1.cjs`: actual Stage7 rich save/download/review/approval/reload/revoke via default transport and real HTTP, with strict nonfinancial fingerprint. The completed five-scenario local evidence is separately recorded in `c1-browser-proof/C1-BROWSER-ACCEPTANCE.md`; it closes that bounded source/UI workflow, not full business UAT.

The source itself distinguishes planned inventory from physical acceptance: `accounting_onboarding.py:132,140,249` always marks physical approval false. Stage16 remains locked at251-254 and in `frontend/src/pages/accounting/onboarding/OnboardingWizardView.jsx`. There is no delivered full16-stage success/sign-off runner or business-acceptance authority that turns these facts into PASS. C2/C3 are being worked separately by root and are not certified by this audit.

An agreed non-production business acceptance plan can execute completed workflows with explicit fixture provenance and independent expected outcomes. It must specify who accepts the results, required scenarios/artifacts, whether actual physical counts are required or a simulation is sufficient, and how Stage16's intended locked behavior is classified. Existing source tests, navigation, and synthetic valuations cannot silently substitute for those missing factual/acceptance decisions. Actual opening/activation and Production acceptance remain prohibited here.

## Search/provenance and limits

Read current source, scripts, workflows, docs, original Track A contract, PR1236 metadata and all1619 returned Issue1006 comments;91 contained Smoke B references. Selected authoritative comments and PR metadata are preserved in `c4-c5-contract-sources.json`. Searches covered smoke_b/Smoke B variants, no-op/nonexistent probe references, UAT/acceptance filenames, readiness flags and physical approval. Absence findings are limited to the retrieved repository/history; no claim is made that an unprovided external runner cannot exist. No new runtime execution or test result is claimed by this read-only audit.
