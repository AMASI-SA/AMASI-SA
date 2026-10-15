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
Task branch: `codex/store-courier-unified-print`.
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
