# Conditional canonical returned-document experiment

Benchmark only. PR-B runtime remains exactly
`617858e5d327d7c1cd898e73ef6aa12e726e567f`. No deployable implementation,
schema change, migration, sync writer change, owner change or production access.
The older README/evidence describe the preceding experiment, not this run.

Treatments: current baseline (known unsafe control), previous canonical
update-then-read, and D (`conditional`): actual `find_one_and_update`, return
AFTER with the existing read projection, reuse existing canonical row/DTO
mapping and delivered guard. All retain the existing merchant owner transaction.
The reservation counter is the same benchmark-only field in disposable fixture
documents as the preceding experiment; it is never added to runtime schema.

D's database predicate binds tenant, order and provider presence. The eligibility
condition is evaluated from that returned document while its transaction holds
the modifying reservation. It deliberately does NOT copy the repository's list
status expression: that expression is not equivalent to assembly DTO.status.
Missing/invalid source fails closed. A rejected delivered operation aborts the
technical increment. No stale DTO or second read supplies the decision.

Safety covers provider-before, during transaction before reservation, explicit
after-snapshot, and reservation-before-provider interleavings on the actual
physical/virtual ASGI route. Provider command dispatch is observed, rather than
inferred from a sleep. Real collection validation failures test rollback.
Concurrent same-piece calls and a later duplicate must produce one transition.
Baseline reproductions are positive-control assertion passes, NOT safety passes.

Linux / Python 3.11 / MongoDB 8.0.12 PRIMARY replica set are mandatory. Three
independent runners execute all designs with alternating randomized order.
Each trial includes 60 distinct cells: physical/virtual times three designs
times ten unique topology/concurrency combinations. The concurrency=1 topology
is identical for one order/one merchant and is measured once. Counts 10/50/100
cover hot order, same merchant different orders, and different merchants.
One-request bursts are not estimates of seasonal capacity.

All failures/timeouts are retained; fixed 90-second request deadline unchanged.
Roundtrips and transaction durations are observed from real command events,
including failed attempts. Performance compares all three designs in the same
new run; prior canonical values are not substituted. Unknown I/O stays unknown.
HTTP uses ASGI, provider transport is synthetic, and sync performance measures
canonical persistence rather than external Salla HTTP. No measured improvement
threshold or success verdict is invented before evidence exists.

No runtime adoption is authorized. If correctness or performance fails, report
the failure; do not tune D, timeouts, retries or the owner protocol to obtain green.
