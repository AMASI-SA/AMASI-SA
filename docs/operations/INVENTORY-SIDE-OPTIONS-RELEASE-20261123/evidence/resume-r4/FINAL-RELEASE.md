# Inventory side options — release completed

**PR2097 is merged, one authorized publication completed, the unchanged original production verifier passed all three probes, the original guard is inactive, and read-only live UI acceptance passed.**

The original production verifier finished at **2026-11-24 00:20:05.996685 UTC** (**03:20:05.996685 Riyadh**). This final evidence follows checkpoint `5a188f4cb0a6888840403a073190d54181532e41`; it does not amend the frozen source or deployment.

## Immutable release identity

| Boundary | Value |
|---|---|
| Reviewed Production base J4 | `28f6d4ca0fc2b7ea7f140432d8e32c768823b6a4` |
| Source A | `deeefceffd9a1e37968513ac4f3833314bb415fd` |
| Source A tree | `a7317c4aa62c3d2266937bf60d4f0b2c4f62a27d` |
| Intent-only B | `a92283678a25494c7d1870cd1675b8f30847aa6a` |
| Merge D | `5cfaac21a1775a0d0761d05aa571f94dbfc02d32` |
| B and D tree | `868b8595dfeba299bf1f2dfaf1fc32b82d357033` |
| Candidate SHA-256 | `f0b6f57069abe12ff6a8720f4eaded7552a47aeeedb0973021249545d726bea3` |
| Release ID | `rg5-ffe0d4437c779b1e455f084be407f8f2d24735f5bf8e994f77c5bf4cbeb1c55f` |
| Frontend artifact tree SHA-256 | `df85c5736ec64571f44a5db506cb0daf34c1ac584ca3115e4e3ce86ebe80898c` |
| Reproducibility proof SHA-256 | `663347f65c3e92073486a66ce3566feb78ceab64b5c0b35ba69b7b21f69518c5` |

B is a direct child of A and changes only `release/release-intent-v5.json`. D has the ordered parents **J4, B** and the exact B tree; [merge proof](publication/merge-proof.json) records the Git-object verification. No squash, rebase or source amendment was used.

## Delivered scope and verified evidence

Options have stable-ID physical, customization and informational roles. Fields explicitly classified as informational no longer determine physical stock identity/readiness or supplier preparation. Existing product policies were not automatically reclassified and no real policy was saved during acceptance. Required physical dimensions, customer answers/costs, revision and stale-draft checks, replay, frozen invoice/receipt history, PR2092 balance recovery and PR2096 supplier-bundle pieces/costs are preserved. Planned cost is not evidence of invoicing or payment.

- **A:** all 43 checks terminal — 37 successes and six intentional conditional skips. Readiness `45284191359` and Operational `45284191276` passed; [complete A evidence](ci-a/README.md).
- **B:** all 54 checks terminal — 42 successes, six expected conditional skips and six superseded cancellations. Latest 22 workflows: 21 successes and the expected TikTok branch exclusion. Readiness `45286310883` and Operational `45286310837` passed; [complete B audit](ci-b/README.md).
- **Tests:** operational backend **6,921 main +151 separate subtests**; demand-read **106**; shipping **16 main +18 separate subtests**; frontend **1,779 in 111 suites**; stock eligibility **5,594**; supplier-bundle Mongo **120**. Browser journeys **8 inventory /13 simple balances /14 financial** passed on synthetic fixtures. Suites overlap and must not be summed.
- **Host Node 20.20.2/24.19.0:** original CI adapters, package boundaries and runtime routes passed. All 387 generated files matched in bytes and modes; package manifests cover 1,821 frontend and 1,338 backend files. The compact B packet documents the omission and digests of redundant generated-output archives.

## Actual-host staging and publication

The first staging attempt stopped during preservation, before starting the original adapter: exit **1**, **95.754 s**, while an unrelated running service changed its files naturally. The failure record and generated backups were preserved; [recovery observation](release/staging-recovery-observation.json). The retained failure-result SHA-256 is `b2b630203cd266f10d2239abcad872804537f914c592dafbc5ebde744d7e5561`.

The reviewed forward resume then ran the original adapter and verifier successfully: **exit 0 in 87.783 s**, exact D/release/artifact/runtime identity and tracked-clean state. It preserved original hashes for **39,615 static paths**; content hashes of **8,700 mutable regular files** were explicitly exempted within the observed live-service scopes. All **48,315 path keys, types and modes** remained fixed. The task did not stop or modify the foreign service. The [successful staging observation](publication/actual-shared-stage-result.observed.json) records these distinctions; its inactive guard observation is historical and is not the final post-publication guard receipt.

Original **prepare passed at `2026-11-23T23:52:04.121715+00:00`** for actor `inventory-side-options-r4`; original **prepublish passed at `2026-11-23T23:52:08.946089+00:00`**, with `ready_to_publish=true`. See [prepublish observation](publication/prepublish.observed.json).

Exactly **one** authorized publication click was observed. The platform event was **“Deployment Started Nov 24, 02:52 AM”** in Riyadh; the observation was recorded at **2026-11-23 23:53:15 UTC**. [Publication-start evidence](publication/deployment-start.observed.json) does not establish completion. No billing change or paid chat was used.

## Final release receipts

| Gate | Result | Evidence |
|---|---|---|
| New platform success | **PASS** — the same event `d0561df2-7853-517d-bea1-eaeba2390333` advanced to **Deployment finished Nov 24, 03:03 AM**, Riyadh | [Platform success](publication/deployment-success.observed.json) |
| Original production verifier | **PASS** — exit 0, `verified:true`, **3 probes**, **770 frontend requests per probe**, exact A/D/release/artifact and original governed JSON bytes/MIME/cache checks | [Verification receipt](publication/production-verification.observed.json) |
| Original final guard | **PASS** — `active:false`; the owned release lease was closed by the successful original verifier | [Final guard in verification receipt](publication/production-verification.observed.json) |
| Live UI acceptance | **PASS**, read-only — inventory entry, classification, purchase and receiving loaded | [Live UI observations](publication/live-ui-acceptance.observed.json) |

The provider's published-version label was `8a74712`; it is not the repository deployment SHA. There was **one publication click**, no repeat publication and no billing change. A browser renderer interruption recovered by reload; the same event's new success was then observed. The platform exposed a runtime-log sample, not a complete Cloud Build command/cwd transcript. Private request logs were not published.

The unmodified command was `python -B scripts/production_release_guard.py verify --url https://mezansalla.com`. Its capture runner first required the task-owned A/D/release lease, then required the original successful three-probe result and final original `active:false` status. It ran from **00:06:02.815061 to 00:20:05.996685 UTC** on 24 November 2026, **843.182 seconds**. Original stdout: **43,130 bytes**, SHA-256 `49e2a33c00be146fe42f5102de06b96ea705471523c394531eac580fe1a96e88`; observed stderr length was zero. Full original outputs remain on the actual host. Two accepted boot timestamps represent distinct runtime observations and do not change the matching release identity.

**Final outcome: COMPLETE. No remaining merge, publication, verifier or UI-acceptance step for this release.**

## Live acceptance and continuity

The live classification editor displayed **مواصفة للقطعة**, **تخصيص يحتاج تجهيز**, and **إضافة جانبية لا تحتاج تجهيز مورد**. Existing size and embroidery roles remained governed. The guide explains that a gift question and its dependent text must both be classified as informational when that is the intended product policy. Dropdowns were inspected without changing or saving them. The purchase page loaded its draft and controls; receiving loaded its filters and finished with an empty selected list. The unchanged classification editor was left open for owner review.

No real policy, stock, invoice, receipt or payment writes were performed for acceptance. End-to-end transaction assertions belong to the synthetic-fixture CI results, not a new production transaction.

**Separate observed issue:** the preexisting **تحديث الأرصدة يحتاج إلى متابعة** warning was visible before publication and remains visible afterward. Its resolution is not established and no financial refresh or recalculation was triggered. See the [prepublication baseline](publication/live-ui-prepublication.observed.json) and [final live acceptance](publication/live-ui-acceptance.observed.json). Neither receipt includes financial amounts or customer details.

[PR2089](https://github.com/AMASI-SA/AMASI-SA/pull/2089) is closed as superseded. Earlier R2/R3 compositions remain historical provenance. The [original source STATUS](../../STATUS.md) is frozen at the source boundary; later state belongs to this evidence checkpoint, PR2097 and the task's own Issue1006 section. Evidence branches are not deployable.

Evidence directories: [A CI](ci-a/README.md), [B CI](ci-b/README.md), [release validation and recovery](release/), [actual-host publication provenance](publication/PROVENANCE.md). Files ending `.observed.json` are root transcriptions of authorized terminal/UI observations, not raw host-file exports. Private inventories and full original host outputs remain on the host. The original local-evidence `STATUS.md` and its checksums remain unchanged as historical packaging evidence; current completion is recorded in this report and `STATUS.json`.
