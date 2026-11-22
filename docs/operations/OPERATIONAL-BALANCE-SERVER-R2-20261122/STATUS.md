# Operational balances server continuation — 22 November 2026

> Historical R2 checkpoint, superseded by OPERATIONAL-BALANCE-SERVER-R3-20261122.
> While its source CI was running, Production advanced to the ordinary PR2076
> merge 02cd99e5b2b68c95341c87590abcf572c885e697. R2 source
> 022224d26d6e77a7af93206d30883c2cf575cbb8 and its candidate artifact are preserved,
> but must not be deployed on the advanced base. No R2 intent-only B was created.
> The statements below describe that earlier checkpoint, not current release state.

## Owner request and completed Android release

The owner now requests the manual server publication code: «ارسل كود النشر بنشر».
Android publication is complete: AMASI-SA/amasi-mobile PR376 and PR378 are merged,
version 1.0.27 / Android30, application source
6634949641c4ca87b002bfcb13434e5a57bc3d21. The exact APK and upgrade certificate
passed the signed receipt in mobile PR377, issuecomment7082744502. The owner's
server-first hold was previously replaced by Android-first; this new request
resumes preparation of the server handoff, with execution owned by the user.

## New source base and immutable provenance

- Task branch: codex/operational-balance-server-r2-20261122.
- Current reviewed Production N: 24270ebdfdebb83282389308a2349cdb27e2b8e4.
- N is the ordinary PR2074 merge; its live source is
  4e6c8a61bd1566e061a4f312e797ce239b582475. Public health observed that identity,
  verified source/critical hashes/frontend, and runtime
  rg5-2e0e3797d6ff3b955a3a23fd31487bd92721e70f316f3bdf34cab0d94c5d630f.
- Reviewed feature source from PR2072:
  db105b1fb7297314b9dbc9a830e8d98bc0211992. Its frozen intent commit
  4f65fef9dfed30f078c13b3b48a8b60f932e541b is preserved and must not be deployed
  on the advanced Production base.
- The inherited intent at N remains unchanged in this source candidate. A new
  source/intent pair and exact-source acceptance are required before publication.

## Integrated scope

Bring the reviewed manual movement reversal, scoped post-movement balances and
optional mobile bank-reference changes onto N. Preserve the deployed customer
confirmation, inventory/purchasing/receiving, demand-read isolation, bounded
workers, financial arithmetic, retry identity and permission requirements.
Do not include PR2076's separate unpublished performance source in this task.

The integration resolves shared workspace and page-policy conflicts. Demand-read
projections must include reversal eligibility and metadata and bind the
account_history parameter to their validated filter/identity. A non-demand-only
merge is insufficient for the current ledger UI. Keep customer collection and
movement reversal out of generic entry permissions; both remain separately
authorized operations. Keep existing CI collection/real-Mongo gates and add the
four existing reversal/history/latency suites from PR2072.

The current inventory payment selector also excludes both sides of an append-only
reversal. The same selector runs for displayed candidates and inside allocation
CAS revalidation. New integration cases cover both execution orders: allocating
first rejects reversal; reversing first rejects allocation. Each rejection leaves
the source and balances unchanged. Existing allocated-payment reversal rejection
is preserved. This compatibility correction is part of the reversal integration.

## Verification checkpoint

Fifteen affected Frontend suites passed 218 distinct assertions on governed
Node22.23.2 / Yarn1.22.22. This includes reversal, current demand views, customer
refund/collection and page permissions. The local dependency manifest and lock
are identical to the previously installed reviewed tree. An initial invocation
named webPageAccess.test.js in the wrong directory; the correct src/lib path and
the deduplicated operation label were rerun successfully. No failed assertion
was suppressed. Workflow YAML parses and retains all N collection gates.

Focused Backend verification passed 101 cases (demand48, reversal effects35,
routes3, account history4, latency11). After the final inventory selector change,
73 cases passed (customer/inventory5, customer collections16, page grants4,
demand48). The two runs overlap in demand48: 126 distinct cases, no failures,
errors or skips. Ten new demand cases cover action projection, snapshot refresh,
confirmed customer money, driver running history and independent grants. Two new
inventory cases cover the allocation/reversal orderings above. Existing CI gates
now require at least demand48 and unified customer/inventory5; no gate is relaxed.

Fresh exact-source CI, including real isolated Mongo return/exchange/inventory
cases, is pending at this source checkpoint. Local tests do not replace those
gates. This document is not a release approval or evidence of a governed build,
host rehearsal, merge or publication.

## Serial host handoff and next steps

PR2074 records accepted original live verify3 and final active:false; its last
canonical record still requests the explicit new Deployment Succeeded UI fact
and release of the serial handoff. PR2076 is separately prepared on N and must
be coordinated before any new shared action. An old inactive receipt is not a
fresh lease check. Automatic continuation remains paused; no autonomous publish.

Finish the scoped backend integration, independent review, exact-source CI and
new frozen A/B proof. Before any host rehearsal, merge or shared staging, reread
Production/task refs and the latest canonical handoff, and require the actual
unchanged guard inactive. If Production advances, re-integrate and regenerate
the source/intent pair; do not use the old intent or downgrade newer changes.
The user executes pinned, hash-checked host code. Rehearsal and shared staging
use different exact-ref fences, preserve all current unrelated files and outputs,
and never reset hard or clear another actor's lease.

Production changed by this continuation: no. No shared /app mutation, lease,
publish, paid Emergent chat, financial reversal/receipt approval or test write.
Issue1006 is at its comment limit; the successor PR is the recoverable checkpoint.
