# Performance & Scalability Engineering Law

**Status: mandatory engineering policy**

This repository must be developed so that application performance remains stable as data volume, concurrent users, and background work grow. Performance is a product requirement, not a cleanup task.

## Non-negotiable principles

1. **Interactive work is protected.** Orders, preparation, review, products, readiness actions, and other user-facing operations must not be frozen by background synchronization, reporting, PDF generation, advertising sync, or reconciliation.
2. **Pagination must be real.** Do not load tens of thousands of records into Python and paginate afterward when the database can filter, sort, project, and limit first.
3. **No N+1.** New code must not introduce per-row/per-workflow network or database reads when a bounded batch/read can provide the same result.
4. **Do not fix performance by hiding data.** Do not lower limits, remove records, weaken filters, or change business semantics merely to make a screen faster.
5. **Heavy work is isolated.** CPU-heavy or long-running work must not block the interactive event loop. External sync must have bounded concurrency, deadlines, checkpointing, and idempotency as appropriate.
6. **Database work is intentional.** Use appropriate indexes and projections based on query evidence. Do not add indexes or caches by guesswork.
7. **Correctness is invariant.** Performance changes must preserve business results, ordering, permissions, accounting, inventory, fulfillment, shipping, review, and financial semantics.
8. **Measure before and after.** Every performance PR must state the baseline, the changed metric, the test dataset/load, and the resulting p50/p95/p99 or deterministic request-count/query-count evidence where applicable.
9. **No half-finished optimization program.** Work is tracked by explicit phases and must be closed with evidence before moving on to the next dependent phase.
10. **Production safety remains mandatory.** Performance work must use a dedicated task branch, Draft PR/checkpoint, tests, rollback plan, and the repository release protocol. Never benchmark by creating uncontrolled Production writes.

## Required performance program

Unless a task is explicitly unrelated to performance, agents must check this program before implementation and mark the applicable phase as planned/in-progress/verified:

- **P0 Observability:** admission wait, execution time, total time, Mongo latency/query count, external API time, event-loop lag, and background-vs-interactive attribution.
- **P1 Data access:** remove N+1, use bounded batch reads, projections, real database-side pagination/filtering, and evidence-based indexes.
- **P2 Dashboard/reporting:** remove duplicate reads and repeated computation while proving exact result equivalence, including currencies, returns, missing-cost cases, and filters.
- **P3 Isolation:** protect interactive requests from long-running Salla/Snapchat/Meta sync, PDF, reconciliation, and reporting work using workers/queues or another proven isolation mechanism with idempotency and recovery.
- **P4 Critical workflows:** orders, preparation, review, readiness, products, receiving, shipping, and other high-frequency paths must remain responsive under concurrent use.
- **P5 Mobile:** reduce unnecessary requests, duplicate mutations, polling, rendering, bundle size, and memory pressure without changing behavior.
- **P6 Peak-load validation:** test realistic seasonal data volume and concurrent interactive/background activity; record p50/p95/p99, errors, timeouts, event-loop lag, Mongo latency, queue/admission waits, CPU, and memory.
- **P7 Platform attribution:** only after application/database/work-queue evidence is healthy should infrastructure/platform latency be escalated as the primary cause.

## Phase discipline

- A task may optimize one phase without implementing every phase.
- Do not start unrelated optimization work and leave a dependent phase undocumented.
- Every optimization PR must identify the phase, scope, baseline, acceptance criteria, rollback path, and exact next safe action.
- A phase is **Verified** only when its acceptance criteria pass on the relevant real or isolated test environment.
- Do not call a performance task complete because a build passes; functional correctness and performance evidence are separate gates.

## Required acceptance mindset

The target is not "fast with today's data." The target is **stable interactive performance as the store grows and during seasonal peaks**.

If a proposed optimization trades correctness, accounting integrity, inventory correctness, fulfillment correctness, shipping correctness, or review integrity for speed, stop and escalate before implementation.
