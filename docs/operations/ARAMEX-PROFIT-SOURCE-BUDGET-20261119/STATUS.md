# Operational profit source budget recovery — 2026-11-19

## Current checkpoint

- Repository: `AMASI-SA/AMASI-SA`.
- Task branch: `codex/aramex-profit-source-budget-20261119`.
- Verified production base: `2c3994193f129a932224aeb0ec805f9d20d5218b` (PR2010 normal merge).
- State: source implementation and independent local review complete; checkpointing source A before the governed build. No frozen B, CI acceptance, release lease, or publication request for this follow-up.
- Production changed by this follow-up: **no**.

## Problem and scope

PR2010 completed the original three-probe production verification with exit 0 at
2026-11-19T03:18:21.022622Z. Its original post-status was `{"active": false}`.
Two subsequent visible operational refreshes saved new reports. Independent
reconciliation accepted the courier fee/payment/allocation mapping and proved
that the second refresh did not add duplicate wallet debits. Private financial
and order evidence is retained outside Git.

The saved profit reports nevertheless exposed a new source-read timeout in the
product-cost component, losing previously available known cost coverage. The
25-second profit budget includes context acquisition before component readers.
This follow-up addresses that read critical path without changing the 25/20/85
second budgets, financial calculations, source coverage, ownership, or writes.

## Completed bounded changes

1. On a positively identified owned visible request with no session, overlap the
   exact settings read/validation with the existing normalized-orders read and
   its conditional raw-orders read. Preserve settings-first error precedence,
   all existing predicates/projections/caps, empty-order short circuit, and
   cancellation/drain ownership. Ordinary/unsupported/session paths stay serial.
2. Opt the operational product-cost caller into the existing guarded parallel
   order-cost-authority reads, retaining its current correction/estimate flags
   and exact cohort. Other callers retain the default serial behavior.
3. Consume the eligible authority cursors through the existing owned Motor read
   helper. Independent review demonstrated that cancelling the original
   coroutine-only authority input helper could release its request while real
   executor reads remained active. The new opt-in expands that inherited gap
   from one to three inputs. The repaired eligible authority and dependent
   estimate reads retain the request and lease until driver work and cursor
   closes drain, including repeated cancellation. The existing nested estimate
   gather preserves its first-observed error. Default callers retain their
   original behavior. Earlier legacy serial catalog/policy reads are unchanged.

## Local verification

The context slice passed 192 affected cases. After the worker-lifetime repair,
the authority slice passed 264 affected cases across nine modules, with zero
failures, errors, or skips. Independent final review passed 103 cases plus five
subtests and reproduced the original full public-builder worker defect against
the repaired code: all three workers and their closes now finish before the
request and lease can be released. The original blocker is closed. These
overlapping counts are not summed. Both new canonical test modules contribute
32 cases each, for 64 exact new cases to require in both canonical CI collectors.
Source-contract checks retain the immutable serial reducer hash and complete
AST equality, allowing only the added keyword/default/forwarding seam.

The independent reviewed source/test diff is 75,798 bytes with SHA256
`348788dd9dac93a8d818b5203a346e91f42b75dd53e9eda7df9a482ed992e5ca`.
It comprises four production modules, two existing source-contract test modules,
and two new canonical operational-balance test modules. The task status is the
ninth source-checkpoint file. No generated release output is tracked.

Controlled frozen-base fixtures preserve full output, query, projection and cap
parity. Context median time changed from 101.431 ms to 61.274 ms for modeled
40/30/30 ms input reads; authority input medians changed from approximately
125 ms to 44 ms for three modeled 40 ms reads. The final authority fixture froze
both full authority/estimate modules and the cooperative reducer from the
verified base, preserving all 17 query/projection/limit operations and complete
ordered output for both eligible database adapters. These establish overlap only,
not production latency or recovery of saved profit coverage. Concurrency is
bounded at two context chains and three eligible authority inputs; aggregate
connection concurrency is not claimed to be unchanged.

## Verification and continuation

The implementation has passed its local review. Release acceptance remains
pending: freeze the unchanged v5 source-A / intent-only-B pair, then accept the
19 applicable workflows on the exact candidate, including Host20/24 readiness
artifacts, real Mongo selections, complete canonical case coverage, worker
lifetime, dashboard contracts, browser and frontend gates. No workflow change
or budget increase is part of this fix. Live recovery remains unproved until a
new saved report restores product-cost source coverage without duplicate
courier payments. The separate provider evidence refresh does not itself prove
resolution of a pending order's financial identity predicates.

Before freezing a candidate, inspect the current production branch again. Apply
the repository's unchanged v5 A/B build and normal-merge protocol. Preserve the
published PR2010 A/B and all other conversations' branches, files, and leases.
No shared `/app` change or rehearsal may start while another lease is active.

Issue #1006 has reached its comment capacity; the task status and task PR are the
bounded continuation records. The next safe action is to checkpoint this exact
reviewed source on the dedicated remote branch, record its full SHA and PR in
the PR handoff, and run the governed local build before creating intent-only B.
