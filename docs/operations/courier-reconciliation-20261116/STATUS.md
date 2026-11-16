# Courier delivery reconciliation — 2026-11-16

## Scope and authorization

Continue the owner's requested Salla/Mezan courier reconciliation and correct
the proved delivery-recognition gaps. Preserve the established rule: a current
delivered order created strictly after operational start may be recognized
without inventing a delivery timestamp. Older orders still need dated evidence
or the existing explicit per-order confirmation. Completed-order shipping fees,
COD custody, booked financial facts, and demand-only refresh remain independent.

Task branch: `codex/courier-delivery-reconciliation-20261116` in
`AMASI-SA/AMASI-SA`. Initial reviewed base:
`71d1dd9010b3fd73de0463a6164f82fcd2b7cff5`.
Latest fetched production base:
`438b461fc660e043726c8dd5ab282d06abd3c17f` (payment-method conflict isolation).
The relevant courier source and existing acquisition test modules are unchanged
between these bases.

## Verified checkpoint: bounded native-evidence recovery

- Added `operational_delivery_order_waybill_unproven` to the existing native
  courier evidence acquisition issue set.
- The actual visible-request entry point now attempts complete, bound native
  evidence reads for that previously omitted issue class.
- Preserved the four-order per-request cap, retry fence, owner filter, complete
  pagination, full canonical-source compare-and-set, and source-only writes.
- Added synthetic public-entry regression coverage in
  `backend/tests/test_operational_balance_courier_waybill_acquisition.py`.
- Included a later generic Salla delivered notice with a null publisher ID and
  different note text; it is ignored by the existing native event classifier,
  while the actual carrier delivery date remains the selected evidence.

Verification used the existing Python environment, with
`PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 PYTHONPATH=backend:backend/tests`
and `python -m pytest --noconftest -q`:

- New regression before the change: **1 expected failure**, no native reads and
  zero candidates despite a genuine waybill-binding failure.
- Final new module after the change: **14 passed**.
- New module plus courier acquisition and delivery-confirmation acquisition,
  excluding the existing real-Mongo scenarios: **85 passed, 17 deselected**.
- Independent in-memory baseline mutation reproduced the expected failure.
- Independent broader run: **75 passed**, with all **17 real-Mongo scenarios
  blocked by connection refusal at the test Mongo endpoint**; these are not
  reported as verified passes.
- `git diff --check`: passed.

## Verified GitHub acceptance — 2026-11-17 (Asia/Riyadh)

Draft PR: https://github.com/AMASI-SA/AMASI-SA/pull/1964

- Reviewed remote source: `2b977a7c486c9121302810b37f46da6b4de085a6`.
- Source tree: `b6cdac3eb499f6e4cff3704ae8db9bff66be419e`.
- Production base at review: `438b461fc660e043726c8dd5ab282d06abd3c17f`.
- All **13 applicable pull-request workflows succeeded**, with no pending,
  failed or skipped workflow in the final exact-source query.

| GitHub backend check | Run / job | JUnit cases | New cases | Previously blocked Mongo cases | Failures / errors / skips |
| --- | --- | ---: | ---: | ---: | --- |
| Operational Balance Backend | 43956199085 / 133611284577 | 5,002 | 14 passed | 17 passed | 0 / 0 / 0 |
| Stock eligibility, reservation and consumption on isolated Mongo | 43956199016 / 133611282904 | 4,716 | 14 passed | 17 passed | 0 / 0 / 0 |

These suites overlap; do not add their totals as unique tests. The focused
acquisition modules contain **102 successful cases** (14 new, 79 existing
courier, 9 manual-confirmation acquisition cases). The 17 real-Mongo cases
include seven-order recovery, source races, ineligible sources, concurrent
acquisition and owner isolation. Their previous local environment limitation
is now resolved by actual isolated-Mongo CI evidence.

Independent reviewer and root both parsed the original JUnit files. The first
artifact's saved source SHA/tree exactly match the reviewed source above; the
second artifact's metadata, checkout and source-recording logs agree with that
SHA. ZIP digests were matched to the GitHub artifact metadata.

- Artifact `operational-backend-results`: ID `14271883448`, SHA-256
  `760b266d7a7b6bc6b5e135684f4441e33bcf7664da03e2ce86d7d8d11295c7b3`.
- Artifact `eligibility-backend-2b977a7c486c9121302810b37f46da6b4de085a6`:
  ID `14272653045`, SHA-256
  `a9945f69492f130b5a7f0680994dd447e36f3699e45edf424e810562a73e6242`.
- Additional log results: 16 shipping/calculator tests plus 18 subtests;
  stock eligibility reports 7 additional subtests. These are not included in
  the JUnit totals above.

## Review and limits

The recovery admission change does not relax native dated-proof validation.
Repeated delivery histories, multiple shipment legs, and conflicting evidence
remain rejected by the existing proof builders.

The initial repeated-delivery hypothesis was narrowed after reading complete
native source samples. A repeated displayed status can be a generic Salla
notice rather than a second carrier transition. The minimized reconciliation
dataset omitted the note and actor fields needed to make that distinction;
raw repeated-label counts therefore cannot predict proof rejection.

No status-only recognition exception, placeholder normalization, or relaxation
of the dated evidence contract was implemented. The speculative local tests
were removed before inclusion. Both `operational_courier_transitions.py` and
`operational_delivery_evidence.py` are unchanged.

Independent review found no blocker in the bounded admission change. Separate
public-entry checks confirmed that generic delivered notices are ignored,
genuine repeated carrier deliveries remain rejected, and a later returned
observation still blocks recovery.

This patch proves recovery for a synthetic, genuinely failed saved binding;
it does not establish the raw canonical shape of every live case, a production
recovery count, or complete courier/financial reconciliation. The read-only
preview database queries did not find the selected live source records and
are not treated as production evidence.

The frozen reconciliation deliverables are unchanged. Live source counts and
financial balances have not been refreshed or reclassified by this task.

## Release and continuity

**Production changed: no.** PR #1964 remains open, Draft, unmerged and mergeable
at the final check. No deployment, release lease, live balance refresh, source
backfill, fee confirmation or financial write was performed. This task did not
change the shared `/app` workspace or another conversation's lease.

The owner explicitly approved the task-branch upload and Draft PR on
2026-11-17 (Asia/Riyadh), resolving the earlier automatic-approval rejection.
The Git CLI then lacked transport credentials, so the authenticated GitHub
integration created the exact reviewed tree and the task branch. A fresh Git
fetch and full-tree comparison verified equality to local checkpoint
`13d8cdbc53768048746248e670047e0a30b1e1e5`; that local history is preserved on
`codex/courier-delivery-reconciliation-local-20261117`.

Canonical implementation branch:
`codex/courier-delivery-reconciliation-20261116` at remote source
`2b977a7c486c9121302810b37f46da6b4de085a6`.

This final status update is retained on a **documentation-only evidence branch**,
`codex/courier-reconciliation-evidence-20261117`, without changing the reviewed
PR head or its verified runtime/test content. Do not use the evidence branch
as a deployment candidate. The PR body records the evidence commit SHA.

Issue #1006 has reached its 2,500-comment limit. Its shared unrelated body was
preserved; the task PR and this status document are the continuation record.

The approved upload, Draft PR and CI-verification scope is complete. Next safe
action: review PR #1964 and obtain separate authorization for integration and
publication if requested. Before any later release, fetch the current production
branch, preserve unrelated changes, recheck applicable gates, and follow the v5
source/intent and shared Release Guard protocol. No live recovery count or
complete Salla/financial reconciliation is claimed from these code tests.

## Publication authorization and preflight — 2026-11-17 Asia/Riyadh

The owner explicitly requested `ادمج وانشر` (merge and publish), superseding the previous upload-only boundary. Publication work is now authorized for PR #1964 using the v5 protocol; the frozen reconciliation evidence and other conversations' files remain preserved.

- Fresh source A: `2b977a7c486c9121302810b37f46da6b4de085a6`; tree `b6cdac3eb499f6e4cff3704ae8db9bff66be419e`.
- Fresh Production J and shared `/app` HEAD: `438b461fc660e043726c8dd5ab282d06abd3c17f`.
- Local source worktree and shared tracked files are clean. Shared Release Guard reports `active:false`.
- Source-A Readiness run `43956199068`, frontend job `133611284942`, passed. Candidate artifact `14269054833` independently validates A/J and full manifests. Intent SHA-256: `71b022cda288daad776a3cc8a0f57c178e622646836da9a9e1ace81a5f160a14`.
- A local governed A/B build using Node 22.23.2 / Yarn 1.22.22 is being prepared before freezing intent B, as explicitly required by AGENTS.md. CI's source-A run did not perform the later reviewed-B clean-clone adapter rehearsals.
- Production changed: **no** at this checkpoint. No merge, new lease, publish, refresh, backfill or financial write occurred.

Next safe steps: finish local pinned build and compare intent with the independent CI candidate; create intent-only B; verify exact-B checks; normal merge preserving A/B; recheck inactive shared guard; perform actual adapter rehearsal; own prepare/prepublish; publish once and run official three-probe production verification. Post-publication UI verification will read the saved courier report. The all-sections refresh can also append eligible automatic bank collection movements and is outside a publication-only verification.

## Intent-only B checkpoint — 2026-11-17 Asia/Riyadh

The scratch dependency fetch repeatedly retried; the local prerequisite was completed in an isolated checkout on the existing host instead. Shared `/app` remained on J with clean tracked files. The isolated checkout used exact source A, independent copied dependencies checked by the pinned frozen installer, two governed bootstrap builds, official artifact verification, and `freeze-intent` outside the worktree. All subprocesses completed successfully. The resulting 1,328,388-byte intent has SHA-256 `71b022cda288daad776a3cc8a0f57c178e622646836da9a9e1ace81a5f160a14`, identical to the independently verified CI candidate.

- Intent B: `e891d675a87134b06e0e0ad4b6d9e1fea0b059b7`.
- B tree: `069adc524c86e5a2b166888c31673c4554991774`.
- Intent Git blob: `8d5ad1a714ea25e94e0cdc1482e4a75b5ed98670`.
- Parent/source A: `2b977a7c486c9121302810b37f46da6b4de085a6`.
- Runtime identity: `rg5-af4b492366600af5c14a6d2599ecc6cc00008f70f826cec8e8854cbdc1edf173`.
- A→B changes only `release/release-intent-v5.json`; root fetched B, fast-forwarded the task checkout, and passed `git diff --check` plus the official tracked-intent Git-relation validator (exit 0).
- PR #1964 is now ready for review. Exact-B Readiness run `43963181648` and other applicable B workflows are running; a separate reviewer is checking B and both actual adapter rehearsals.
- Production changed: **no**. No new lease or publish. Do not squash/rebase the frozen A/B pair.

Next safe action: require the final exact-B checks and independent review; fresh-check Production J and the shared inactive guard, normal-merge the immutable pair, then actual shared adapter rehearsal and guarded publication. The UI verification scope remains saved-report reads, without all-section refresh or manual financial assertions.

## Normal merge completed; live publication pending — 2026-11-17 Asia/Riyadh

PR #1964 was merged with the normal merge method and expected B head after all 13 applicable exact-B workflows succeeded. The unrelated TikTok Creator candidate workflow was conditionally skipped. Independent review verified both actual Node20/24 clean-clone adapter rehearsals in Readiness `43963181648` (jobs `133636216307` and `133636216161`).

- Merge M: `b439b683f26ffdd61b7b9de80315ea21747d135c`.
- Parents: Production J `438b461fc660e043726c8dd5ab282d06abd3c17f` and intent B `e891d675a87134b06e0e0ad4b6d9e1fea0b059b7`.
- Merge tree: `069adc524c86e5a2b166888c31673c4554991774`, exactly equal to B.
- Fresh fetch, B↔M empty diff, A ancestry, and A→M intent-only diff all passed (exit 0).
- Shared host before handoff: J, tracked-clean, Release Guard inactive. The 120 unrelated untracked files were recorded with aggregate manifest SHA-256 `ff7a5c328699b9ab8bc249f4b182e33d3531e3ecd07342ae8246b0eceac56739` for preservation comparison.
- Production source branch changed: **yes**, to M. Live Production runtime changed: **no** at this checkpoint; no publish/lease has occurred.

Next safe action: require inactive guard, fast-forward shared `/app` to exact M, execute the actual `cd frontend && yarn build` adapter rehearsal, verify preserved untracked content and exact identity, require the Production-push checks, then own prepare/prepublish and one authorized publish. Official three-probe verification and saved courier UI inspection remain mandatory after publication.
