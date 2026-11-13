# Operational balances live refresh follow-up — 2026-11-13

## Current state

WIP source checkpoint. This branch is not a reviewed release candidate.
The earlier PR1902 release is officially verified live and its matching lease
is closed. Fresh financial acceptance still fails into the dated saved report.
This follow-up preserves that distinction and must use a new v5 source/intent
pair before any further publication.

## Scope and ownership

- Repository: `AMASI-SA/AMASI-SA`.
- Task branch: `codex/operational-live-refresh-followup-20261113`.
- Base Production: `8d25f7a6fa9a310b319b753c32457e70987f7c51`.
- Root owns integration, GitHub writes and any authorized release operation.
- Frontend slice: `PendingOrdersDialog.jsx` and its focused test file.
- Backend slice in progress: bounded cost-authority reads, preserving the full
  order cohort and all financial holds. No backend WIP is included in this
  initial frontend checkpoint.
- No coordination marker exists; no new coordination board was initialized.
- The exact checkpoint SHA is recorded in the PR continuation entry, avoiding
  a self-referential tracked commit identifier.

## Completed source behavior

The pending-order dialog could show a confirmation-in-progress message on an
idle row whose confirmation target was absent. Both its target key and the
initial busy key were null. The render condition now requires a non-null busy
key before displaying the message. This does not change confirmation targets,
eligibility, permissions, saved proofs, or balance amounts.

The added focused cases cover absent and malformed confirmation metadata,
continued display of the order number and reason, no automatic preview or
confirmation call, and the real busy state during an explicit confirmation.
All test fixtures are synthetic.

Changed files in this initial checkpoint:

- `frontend/src/pages/operational/PendingOrdersDialog.jsx`
- `frontend/src/pages/operational/PendingOrdersDialog.test.jsx`
- This status document.

## Verification and actual observations

- Direct authenticated inspection reproduced the false idle busy label.
- Source inspection confirmed the null equality cause and that merely opening
  the dialog does not initiate preview or confirmation requests.
- `git diff --check`: exit 0 for the frontend slice.
- Repository-pinned toolchain bootstrap: exit 0; Node22.23.2 / Yarn1.22.22.
- The standard frozen-lockfile dependency installation is still running in
  the task checkout. The focused frontend test is PENDING. This WIP is not
  claimed tested or ready to merge.
- No customer, order, party, or financial data is included in this checkpoint.

The live report renders saved balances, pending-order details and pagination.
Its initial visible refresh failed with a source-read message; two later
controlled visible requests used the refresh-timeout fallback message. A saved
financial-position GET was observed with status200 and duration31839.360ms.
Available Management logs did not expose the corresponding refresh stages.
No single production bottleneck has been established.

## Backend work still in progress

The full order cohort must remain available to financial authority checks:
newer orders can supply bundle classification and cross-order allocation
conflicts affecting older orders. A proposed historical-only filter was
rejected after an actual-module synthetic regression demonstrated changed
financial output. It must not be included in the candidate.

The remaining narrow proposals are to omit an empty-plan units query and, if
proven safe, overlap independent bounded reads only for the ordinary raw
database under its owning visible request. Wrappers, snapshot sessions and
unrelated callers must retain serial behavior. Failure, cancellation and
repeated cancellation must join every started child before request completion.
Deadlines, auth, monetary rules, proof requirements and source completeness
limits are unchanged. No scheduler, polling, detached work or hidden refresh
may be introduced.

## Release state and next safe action

- PR1902 source: `1f8f1d673854f594086a6e87dec6c98633da0061`.
- PR1902 deployment: `b64121c265d8d6e158df0d9fb88debc52c95317d`.
- Official original-operator verification completed2026-11-13 at17:02:16UTC,
  exit0, three full checks,582 frontend requests per check. Subsequent guard
  status was `active:false`.
- Previous release evidence is preserved on documentation-only branch
  `codex/operational-delivery-release-handoff-20261113`, checkpoint
  `e1eb7af40d5604a77b93f92f8dfaa071c8b660d4` and PR1902 continuation comments.
- Do not modify or republish the frozen PR1902 source/intent pair.
- New task Production changed: NO. No new lease, operator source update or
  Publish action has occurred. No real financial confirmation was clicked.

Next: finish the single dependency installation, run the focused frontend
suite, complete and independently review the backend slice, and preserve a
combined source checkpoint. Recheck latest Production and required gates.
Only a fresh reviewed source/intent pair, exact-head gates, clean-clone
rehearsals, fresh operator coordination/status and the user's existing release
authorization can proceed to publication. Success still requires a fresh live
financial report without a dated fallback; release identity verification alone
does not satisfy financial acceptance.
