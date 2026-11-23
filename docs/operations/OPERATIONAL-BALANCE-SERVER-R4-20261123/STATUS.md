# Operational balance server R4 — current-source integration checkpoint

## Scope and authority

The owner requested manual server publication code after distribution of
operational Android 1.0.27/code 30. Preserve that completed mobile release.
This task supplies manual movement reversal eligibility/command, full scoped
running account balances and optional mobile bank references. It does not run
live financial tests, approvals, reversals, sync or backfill.

Repository: AMASI-SA/AMASI-SA. Product branch:
`codex/operational-balance-server-r4-20261123`.

## Resume evidence and revised base

- Actual owner host status: original guard inactive; tracked source clean;
  branch `hotfix/prod-snap-meta-final`; HEAD
  `44122bcc986bd1dff1532b9ad62c0268e899a179`.
- Fresh GitHub Production ref agrees with that HEAD. It is ordinary PR2081
  merge D, ordered parents `02cd99e5b2b68c95341c87590abcf572c885e697` and
  `9e77b056d540dfef13d928af088941c5f88a65cb`, tree
  `0435b8d41ce261f658c953e41e35b739e57e8963`.
- Prior PR2080 remains Draft/open/unmerged at immutable intent-only B
  `9a500a21013a08f750009be656362481986eb208`. Its source A is
  `dd3a9ea29fce28e2fa87332cff4a6d46f776e0d3`, based on previous Production
  `02cd99e5b2b68c95341c87590abcf572c885e697`.
- Prior all-exact-B technical acceptance is preserved at evidence-only commit
  `34ab7a708b8da7a92160ef93850acee4a1425db8`, folder
  `docs/operations/OPERATIONAL-BALANCE-SERVER-R3-20261122/`. Those results are
  historical; they do not certify this newly integrated source.
- No prior frozen source/intent/evidence commit is rewritten. No stale intent
  is reused. Current R4 inherits the exact current Production intent unchanged
  until a fresh two-build candidate is independently accepted.

## Integrated source and invariants

Ported the 31-path reviewed source delta, preserving every PR2081 change. The
only textual conflict was the Operational workflow collection gate: keep all
three new customer refund/access/mobile selectors and increase the existing
unified activity minimum from 3 to 5. No current gate is removed or relaxed.

Preserve independent reversal permission, own/manual/eligible movement limits,
append-only linked inverse, reason/review/confirmation, stable retry identity,
resource scope and pre-commit authorization. Reversed supplier payments remain
excluded from allocation and allocation CAS revalidation. Full scoped account
history precedes filtering/pagination; bounded demand workers and current
first-visible-read performance remain intact. Keep the delayed-refresh ledger
dialog repair and its regression case. Preserve all current Mezan86 provider,
refund, employee, assembly and assignment behavior.

The port is a fresh source commit descended from current Production, not a
merge of the old intent branch. Temporary merge metadata was removed. Current
Production's inherited `release/release-intent-v5.json` is unchanged.

## Verification and release state

Initial static checks: all changed Python files parse; staged diff whitespace
check passes; inherited intent diff is empty; no pending merge metadata.
Focused integrated reviews/tests and full exact-source CI are pending.

This checkpoint is WIP, not a ready-to-publish release. Exact canonical source
SHA, test results, new candidate/intent B and review evidence will be recorded
in the task PR and evidence-only checkpoints after creation/verification.

The owner-supplied status is informational: it proves neither that PR2081 was
published nor that its original verification completed. The latest PR2081
comment read during resume still reports publication/verification pending;
independent handoff review is in progress. Issue1006 comment6238987123 remains
stale at 2026-11-22T21:14:09Z, with no PR2081 section. No new approval gate is
invented; actual current coordination and original guard govern host work.

Next safe actions: finish integrated reviews/tests, create a Draft successor PR
with exact canonical identity, accept fresh v5 candidate then intent-only B and
all applicable exact-B gates. Resolve the latest predecessor handoff from
authoritative evidence. Before any actual host rehearsal, read the unchanged
original guard afresh and require inactive. An isolated original-adapter host
rehearsal must pass before ordinary tree-equivalent merge and separately guarded
staging/prepare/prepublish; owner publication then requires new Deployment
Succeeded and original three-probe verification with final inactive status.

Production changed by this task: NO. Shared host source/outputs changed: NO.
Own lease: NONE. Publication: NONE. Automatic continuation remains paused.
Old PRs2072/2078 are superseded/closed; PR2080 will be marked superseded only
after this successor is durably recorded. Keep all foreign files/leases and
unrelated task records unchanged.
