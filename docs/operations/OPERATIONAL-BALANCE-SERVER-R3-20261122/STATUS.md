# Operational balance reversal server handoff — R3

## Owner instruction

The owner requests manual server publication code: «ارسل كود النشر بنشر».
Android 1.0.27 / code30 is already distributed with the reversal UI. This task
prepares its server capability, scoped account running balances and optional bank
references. The owner will execute the reviewed terminal handoff and publication.
Do not rebuild Android, use paid Emergent chat, or create live financial tests.

## Current source base and immutable provenance

- Task branch: codex/operational-balance-server-r3-20261122.
- Reviewed Production N: 02cd99e5b2b68c95341c87590abcf572c885e697.
- N is the ordinary PR2076 performance merge, with tree
  8f915a681de43fed9da1ec98685a56f87a4c8bcb and parents
  24270ebdfdebb83282389308a2349cdb27e2b8e4 and
  5bb05036d8095a87c6635c0a431c2c19d24d8f78.
- Earlier feature source db105b1fb7297314b9dbc9a830e8d98bc0211992 / PR2072
  and its B4f65fef9dfed30f078c13b3b48a8b60f932e541b remain immutable history.
- R2 source 022224d26d6e77a7af93206d30883c2cf575cbb8 / PR2078 integrated
  those features with the current demand worker and customer/inventory services.
  Its local 126 distinct Backend cases and 218 Frontend assertions passed.
  Its candidate was accepted by original validators but became stale when N
  advanced during exact-source CI. No R2 B was created; never deploy its candidate.
- R3 inherits N's tracked intent unchanged. Fresh source CI and a fresh governed
  candidate, intent-only B and exact-B CI are required. Never reuse either old pair.

## Preserved and integrated behavior

Keep all N performance behavior: owner ledger directly uses one validated ledger
job after bootstrap; reports request the visible view first; bounded early polling
retains one consumer and serial backpressure; the raw stdin runner watcher retains
parent-EOF cancellation while allowing ordinary child shutdown.

Add append-only manual movement reversal with reason/review/confirmation, stable
retry identity, original-to-counter-entry linkage and independent own-movement
permission checks. Demand read snapshots project the same eligibility and reversal
metadata. Bind account_history to immutable demand identity and compute running
balances across scoped pre-filter history before pagination. Keep optional bank
references accepted in the existing mobile entry paths.

Retain all customer refund/collection routes, badges and permission boundaries and
inventory/purchase/receiving behavior. Exclude both sides of a reversed supplier
payment from allocation, including CAS revalidation. Allocating first still blocks
reversal; reversing first blocks allocation without a second balance effect.

Workflow integration retains every N test and count gate, including three runner
lifecycle cases, and adds the reversal/history/latency suites. New demand and
customer/inventory collection minimums are48 and5 respectively; no gate is relaxed.

## Verification at this source checkpoint

Fresh Frontend integration passed 101 cases in seven affected suites on governed
Node22.23.2/Yarn1.22.22, with no failures or skips. A new owner-ledger page-two
reversal case proves a fresh scoped demand, preserved pagination and no extra
resource/global/statement read. Strengthening it with a delayed response and
dialog.open exposed a real remount defect: the successful reversal detail was
recreated closed after the owner resource returned. The original test failed only
that assertion; adding resource readiness to the existing dialog effect made the
same test and all seven suites pass. No permission or read-validation change.

Fresh Backend integration verified 214 distinct cases across a disposable owned
loopback Mongo replica and actual worker child processes: all seven demand suites,
reversal/history/latency, inventory allocation/receiving, customer collections and
page grants. The first run passed212 and failed only the two actual-child cases
because the local test virtualenv lacked the repository-pinned python-dotenv1.2.2.
After installing that declared dependency, both cases and the three lifecycle
cases passed (5/5); source/test hashes were unchanged. The initial failure and
focused recheck evidence are retained, and the counts are not added as217.

Independent integration review and workflow comparison pass. Every N gate is
retained. All reviewed R2 Backend changes are byte-identical; the runner and its
lifecycle cases remain N's exact bytes. Original R2 exact-source CI also finished
with23 successful checks and6 conditional skips, but is historical only.

Fresh R3 exact-source CI, build candidate and B are pending after this source
checkpoint. Local or CI evidence does not represent an actual Emergent rehearsal.

## Serial host coordination

PR2074 is complete: receipt b46798fab681471a0fdf9450c30d6446a4f55ffa
records owner-confirmed new Deployment Succeeded, original verify3 and closed
guard. Its old UI/lease blockers are superseded.

PR2076 owns the current serial host handoff. Its owner-run isolated rehearsal
passed at evidence51b3ba6fe1b0fa87db5c4897cb2fff66ca4d53a2, and its ordinary
merge is N. Its staging, original prepare/prepublish and live completion are
separately coordinated. This task must not overlap that host work, clear a foreign
lease, or rerun its completed steps. Wait for its authoritative completion and
released handoff, then read the unchanged actual guard afresh before rehearsal.

Next: finish R3 review/tests; create and checkpoint its source; accept exact-source
CI/candidate, freeze intent-only B and accept exact-B CI; then prepare a pinned host
command using current completion evidence and fresh refs. Rehearsal and staging
have different ref fences. Preserve all unrelated host files/settings/outputs.

Production changed by this task: NO. No host source mutation, lease, publish,
financial reversal/receipt approval/sync/backfill, or production test write.
Automatic continuation remains paused. PR bodies are durable coordination records
because canonical Issue1006 has reached its comment limit.
