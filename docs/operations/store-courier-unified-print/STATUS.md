# Store courier unified print — implementation checkpoint

## Contract

- Owner authorized implementation and a reversible release on 2026-10-15.
- Courier printing requires the **current Salla status to be completed**, even
  for reprints after shipping/delivery. No inference from local completion.
- Mezan assembly completion and caller permission gates remain in place.
- Courier documents do not require an external shipment ID, AWB, or PDF URL.
- Carrier issuance and read/print use the same current-provider discovery and
  formatter. Missing order-level carrier fields can be resolved from the
  current outbound shipment; explicit external carrier evidence wins.
- External carrier issuance and official PDF handling remain unchanged.

## Baseline and scope

Base: `2196670da3a5cbf47ac633548a344529dd5b41f7` on
`origin/hotfix/prod-snap-meta-final`.
Task branch: `codex/store-courier-release-20261015`.
Worktree: `D:/amasi-build/store-courier-unified-print`.
Changes are limited to the shipping service, regression tests, and this record.
Existing mobile Build43 edits and unrelated Qoyod changes were not modified.

## Evidence

New regressions failed on the baseline: courier discovery from shipment-only
identity, completion enforcement on print reads, conflicting external carrier.
After implementation: 210 passed / 151 skipped across the unified-print,
legacy print, print boundary, current-label guard, fulfillment-label and
snapshot-bootstrap suites. Separate cross-task eligibility: 1 passed / 30
skipped. Skips include unavailable isolated replica-set fixtures; no live
provider calls were made by these tests.

Runtime used: `D:/codex-test-runtime/review-minimal-shipping/venv/Scripts/python.exe`.
Set PYTHONPATH to this worktree's `backend` and `backend/tests`.
Run pytest with `--asyncio-mode=auto` on the seven named suites above.

## Remaining release gates

- Review the combined diff and CI, including replica-set tests.
- Verify deployed identity and acquire no other conversation's release lease.
- Freeze the governed source/intent pair, rehearse clean clone, then use the
  repository's current Release Guard prepare/prepublish/verify protocol.
- After deployment, exercise the authorized order in the emulator to the
  actual PDF viewer, and compare the web document. Do not confirm physical
  printing/QR attachment without real evidence.
- Production changed: **no**. No merge or deployment at this checkpoint.

## Rollback plan

Before deployment, record the verified live release identity and source SHA.
Keep this change as an isolated source commit. If UAT fails, revert that exact
source commit on a new task branch based on the latest production branch;
do not reset the production branch or discard other conversations' commits.
Run affected tests, create a new governed source/intent pair, then deploy the
revert through the same lease, prepublish, and three-probe verification gates.
Retain the previously validated mobile APK; this backend patch requires no
mobile APK replacement.

No schema migration or bulk data backfill is part of this fix. Code rollback
does **not** reverse a Salla status transition already completed during a
normal issue action, delete labels, or reset operational history. Any data
correction must be assessed separately, not guessed as part of code rollback.

Rollback triggers: valid courier cannot open its current PDF; a courier PDF
opens before confirmed Salla completed; wrong order/recipient/carrier appears;
official-carrier label regression; release identity verification fails.

## Release preparation update 2026-10-15

Source 0b15a6b756aa00c775d7cd1f32a62f398e73f397 passed all active PR checks.
Shipping replica-set run 38752709990: 461 passed, 1 skipped.
Production advanced to 231f5714257220cdfe192428275c2bca62e0bd2d (products PR1371).
Merged that baseline without conflicts; the old candidate intent is superseded.
A new governed build and intent are required before publication.
The owner reiterated rollback authorization on any new-code printing regression.
Rollback implementation target is the isolated fix commit on the release branch,
reverted on latest production, preserving subsequent unrelated work.
Production changed by this task: no. No lease acquired or publish attempted.
Next: fresh combined CI, new reviewed intent and rehearsal, guard gates, emulator PDF UAT.

Release guard rejected the merge-history candidate: inherited intent history crossed the new baseline. Rebuilt the same source changes by cherry-pick on clean production baseline 231f5714257220cdfe192428275c2bca62e0bd2d, without weakening any guard. Previous PR1370 is superseded by this release branch. No production deployment.


## Published verification and emulator acceptance — 2026-10-16

PR1373 merged; deployment commit f174e294d12e2d96f0ba03678c4e4768036bea2d.
Source A: 21a33af8d99f0bfb8869b057c5a7c6d0c58864d7.
Intent-only B: a0b335dcf157c5af9378bea6f50fdfa5d31c2626.
Final source/intent CI and Linux clean-clone rehearsal38805661588 succeeded.
Emergent /app matched the exact reviewed tree. Adapter initially refused generated
backend bytecode; one untracked cache directory was preserved outside /app/backend.
Rebuild with PYTHONDONTWRITEBYTECODE=1 succeeded; no guard/source relaxation.
prepare and prepublish succeeded, actor store-courier-1373. UI publication started
2026-10-16 00:04 Arabia Standard Time. The current UI uses a green "Deployment finished"
marker (not the historical literal "Deployment Succeeded"); a newer marker appeared,
Live publication identifier changed from88c0fd7 to4c6e3b8, and runtime started anew.
Do not describe the literal older UI text as observed.
Guard verify exit0, verified:true, three consecutive full checks; subsequent status active:false.
Release: rg5-96eeae9285badaa5b06ccfc6f7346cb09e2daa03bfedaa4e52ce16a4d6e53be0.
Public health independently confirmed exact source and verified identity.
Production changed by this task: YES, evidenced by the release identity above.

Android emulator5554 / com.amasi.sa.mobile versionCode43: original blocked issue action
now returned a ready courier label and opened the actual Google PDF viewer. After
returning to assembly, a fresh order search and print action opened the correct PDF
again. Recipient fields, courier, order QR/number were visible. No physical printer
job, QR receipt confirmation, or shipment handoff was performed. No document was sent.
Customer/order-specific visual evidence remains local outside GitHub, not committed.

Separate existing layout limitation observed: long address text is clipped in the
installed mobile PDF template. carrierLabelPrint.ts combines formatted address plus
component fields and uses .row max-height:0.42in; overflow:hidden. This mobile file
was not modified by this backend release. Both prior internal formatter and new
shared path call _store_courier_print_data. No claim that long-address layout was fixed.
No rollback triggered: the original issuance failure is resolved and repeated PDF
opening passed; the existing layout limitation is disclosed for separate mobile work.

Rollback remains authorized if a regression is established. Revert isolated source
e2a90eae7e0650fbab732f72f66201e76a01d2f5 and companion test-workflow change as needed
on latest production, create a new governed source/intent pair and republish through
all gates. Do not reset production or undo Salla transitions/data as code rollback.
Prior live source: e34fa4c967d39a22a4e4a1ccb7a998698f194890.
Local inverse patch git-apply-check passed; SHA256:
B044674EE52D603D86C47C8412DC6A815FE6CCA9DE87CF342B63B4C64C4362A2.
This documentation checkpoint is separate from the frozen production release.
