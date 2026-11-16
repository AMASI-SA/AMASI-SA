# Payment-method discovery — completed production release

## Outcome

Task `PAYMENT-METHOD-DISCOVERY-R2-20261116` completes the payment-method discovery feature introduced by [PR1962](https://github.com/AMASI-SA/AMASI-SA/pull/1962), with the live acceptance correction in [PR1963](https://github.com/AMASI-SA/AMASI-SA/pull/1963).

Observed payment methods appear automatically under Operational Balances → payment fees. The dashboard warning links to each unresolved method's editor. Percentage, fixed SAR fee and commission VAT remain unspecified until the owner supplies complete explicit terms. Explicit zero is supported; unknown fees are never inferred as zero.

A pre-existing alias conflict in one method previously made every editor immutable. R2 keeps that conflict local to its own row, so unrelated new methods remain configurable. Genuine incomplete reads still close editing globally.

Production version **18525af** is published. Live acceptance passed. The unaltered official production verifier completed with **exit 0, verified:true, all three probes**, exact reviewed identity/artifact, and final official **active:false**. The release is complete.

## Immutable reviewed release

| Item | Value |
| --- | --- |
| Repository | `AMASI-SA/AMASI-SA` |
| Production branch | `hotfix/prod-snap-meta-final` |
| Frozen runtime branch | `codex/payment-method-discovery-r2-20261116` |
| Source base J / previous merge | `71d1dd9010b3fd73de0463a6164f82fcd2b7cff5` |
| Source A | `39c891d7b1da06d01e75b71bb42310eb8d29f066` |
| Source tree | `03491ab6bd54b4a8a9e0db9a6fb0f9073eb19db4` |
| Intent-only B | `ec6b3a1b808ca7bb4af081f7c63ea28796d89617` |
| B / M tree | `70842fa4b3db6c9cea6a4b4d743a804f078f7e52` |
| Production merge M | `438b461fc660e043726c8dd5ab282d06abd3c17f` |
| Runtime ID | `rg5-ddac746f37c8ee913405c42ced5804aa9e68ff17b56ba60a60ff2a824a7ffe8e` |
| Frontend artifact SHA256 | `a31d130116eaa72d7ba3a0ec0f36fda19c29db272d207642d520482df16e46ff` |
| Reproducibility proof SHA256 | `1a7f37962dc5acfe31e346f90d3c75e259b49361fda98c29d890ce92b8a3167f` |
| Intent SHA256 | `a421b93d18b796adffc567f8c691721fb53d1c0844603ea54ff2c154d2025480` |

A has the identical source tree to locally tested commit `07e51e7199009f484a26df24223bc4f6a787b058`; only commit metadata differs. A → B changes only `release/release-intent-v5.json`. M was a normal merge with parents J and B, and its tree equals B. The official Git transition validator accepted B and M. No squash, rebase or amendment of frozen source/intent commits occurred.

## Repair and preserved behavior

`backend/payment_method_setup.py` no longer turns global directory completeness off when one row has ambiguous fee aliases. Completeness represents settings/directory read integrity. The conflicting row retains unspecified terms and `editable:false`; the unchanged save path rejects ambiguous or inherited targets with HTTP409. Unrelated rows retain their existing authorization and compare-and-set protections.

Timeouts, overflow, failed reads, unavailable or foreign settings, and duplicate owner settings still disable editing globally. This correction does not alter discovery queries, fee matching precedence, financial calculations, provider policy, source-mode selection, frontend implementation or refresh cadence.

The feature uses the existing `settings.payment_methods` calculation authority. Discovery reads owner-scoped method names from saved orders, with a 250-group bound and two-second query limit; it does not create settlement accounts, ledger entries, guessed fee rules or a new polling loop. Existing custom methods remain available.

Changed source files:
- `backend/payment_method_setup.py`
- `backend/tests/test_payment_method_setup.py`
- `frontend/src/pages/operational/ObservedPaymentMethods.test.jsx`
- Task STATUS

## Focused validation and independent review

The three targeted backend cases first failed against the previous source because unrelated rows became immutable. After the repair:

- Backend setup suite: **51 passed, zero failures, exit 0**. Covers both alias orders, independent method save/readback, byte-for-byte BSON preservation of other settings, HTTP409 conflict rejection with no write, owner scope, true read failures, overflow and revision/CAS protections.
- Frontend observed-method suite: **13 passed, one suite, exit 0**. Covers the exact-method deep link, mixed locked/editable rows, three initially blank fields, explicit synthetic terms and expected revision, authoritative save confirmation, and preservation of the conflicting row.
- Independent backend/source review approved with no material finding. Root reviewed both implementation/test diffs; `git diff --check` passed.
- Tests use synthetic data. No real commission was entered or saved during acceptance.

Governed Node 22.23.2 / Yarn 1.22.22 source-A bootstrap produced two matching builds. Frontend verification, external candidate freeze/validation and official Git transition checks exited 0. The shared configured adapter used the reviewed backend origin and also exited 0; generated backend release identity equals the frozen intent.

## Exact release CI

Every relevant check was tied to the exact frozen commit, not inherited from an earlier run.

- Exact B: 35 returned records — **24 success, 8 skipped, 3 cancelled**, with zero failed/pending. The three cancellations were superseded Snapchat Direct run 43947081870. Replacement 43947133001 on the same B succeeded at scope, with two intentionally skipped out-of-scope jobs.
- B Readiness 43947081805 passed both actual clean-clone rehearsals: Host 20.20.2 job 133580156661 and Host 24.19.0 job 133580156496.
- B Four Platform Spend 43947081853 and Operational Balance Web 43947081942 passed.
- Exact M: **11/11 successful**, with zero pending, failed, skipped or cancelled. Readiness 43948563014 (7), Security 43948562868 (2), CodeQL 43948563334 (2).
- M actual clean-clone jobs 133585156829 and 133585156608 verified exact A/M/runtime/artifact, health and isolated runtime artifact. Last CodeQL Python job 133584631274 completed 20:27:30Z.

## Publication and live acceptance

The shared checkout was normally fast-forwarded J → M only after an in-command fresh official inactive-guard check, expected HEAD check, clean tracked/staged diffs, unchanged remote target and reviewed tree verification. Unrelated untracked files were preserved. The configured build completed before preparation.

Official prepare created the task-owned lease at **2026-11-16T20:23:52.530472+00:00**, actor `payment-method-discovery-r2-20261116`, bound to the exact A/M/runtime above. Prepublish exited 0 with `ready_to_publish:true`.

One R2 **Publish changes** action was submitted. Emergent operation `c1201ebe-8a1a-5e5b-866e-1a6021ee12b4` displayed **Deployment finished Nov 16, 11:39 PM** (Asia/Riyadh), new successful version **18525af**, after prior version 0680799. No second prepare, Publish, interrupt, abort or lease replacement occurred.

Live acceptance on `https://mezansalla.com/operational-balances/provider-fees?method=paypal` confirmed:
- The automatically discovered PayPal row shows “العمولة غير محددة”.
- Percentage, fixed SAR fee and commission VAT are blank, required and enabled; Save is enabled.
- The prior global incomplete-read warning is absent.
- The dashboard notice's exact PayPal link opens that editor successfully.
- The notice is outside the dashboard columns. At the existing desktop viewport, the advertising card measures 460 px and its table 422 px, preserving the compact layout.

No live fee field was typed into, and Save was not clicked. The actual merchant fees still require owner entry.

## Official production verification

The already-running, unaltered official command `python -B scripts/production_release_guard.py verify --url https://mezansalla.com` started at **2026-11-16T20:40:29.536185+00:00** and completed normally. Its result and final status were observed before **2026-11-16T20:53:27Z**.

- Verification exit: **0**.
- Result: **verified:true**, **checks:3**.
- Source A, source base J, deployment M, runtime ID and frontend artifact exactly match the frozen identity table above.
- Frontend checked requests: **590 per probe**, covering the official canonical/cache-busted artifact contract.
- Boot observations: `2026-11-16T20:38:38.817400+00:00` and `2026-11-16T20:38:42.394276+00:00`.
- Final official status command exited **0** and returned **{"active": false}**.
- The successful verifier closed its matching owned lease. No restart, abort, manual lease removal, second verification invocation or repeated Publish was needed for R2.

Receipt directory in shared environment: `/tmp/mezan-payment-method-release-1963/`.
- `before-update-status.json`: initial official inactive status.
- `adapter.log`, `adapter-exit.json`: configured build and identity result.
- `prepare.json`, `prepare-stderr.log`: owned lease.
- `prepublish.json`, `prepublish-stderr.log`: ready-to-publish check.
- `verify.json`, `verify-exit.json`, `verify-stderr.log`: final official receipt.
- `final-status.json`: final official guard status.

## Final handoff

This STATUS is on the separate documentation-only branch `codex/payment-method-discovery-r2-handoff-20261116`, parent checkpoint `45339627af46693187866262652ee4f03299a3c9`. **Never merge or deploy the handoff branch.** It differs from the frozen runtime only in this task STATUS. Production remains M, and the frozen runtime branch remains B.

The preceding PR1962 / version 0680799 passed its own official three-probe verification after one startup HTTP 520 recovery; its live acceptance exposed the row-conflict defect corrected here. R1 frozen B1 remains `03605c83e67c26b8f92477624f92d662fb97616e`. R1 receipt is `/tmp/mezan-payment-method-release-1962/verify2.json`; no interrupt or abort occurred.

Issue 1006 reached its 2,500-comment cap, so the task STATUS and PR bodies provide the durable record. The release is complete; no further source synchronization, prepare, publication or verification is required for this reviewed release.

No commission, payment, ledger entry, advertising budget, campaign or provider settings were changed by this task. The remaining merchant action is to enter actual fees for unresolved methods through the existing settings UI.
