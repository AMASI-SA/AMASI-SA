# Conditional canonical returned-document experiment

Benchmark only. PR-B runtime remains exactly
`617858e5d327d7c1cd898e73ef6aa12e726e567f`. No deployable implementation,
schema change, migration, sync writer change, owner change or production access.
The older README/evidence describe the preceding experiment, not this run.

## Final result: do not adopt D in PR-B

Tested HEAD `a0730a10bb9ccc8c6bc0d71f64ac63867dbe7f6d`.
[Run 37936511717](https://github.com/AMASI-SA/AMASI-SA/actions/runs/37936511717)
completed all three independent trials; CI conclusion **failure** is retained.
No D tuning or rerun followed this measured failure. The final evidence-only
commit is not a new test result and intentionally skips CI.

All 180 measurement cells completed: **174 pass / 6 fail / 0 skipped**.
Safety: 84 cases per trial, 252 assertion passes; these include 54 deliberately
unsafe baseline positive controls and are not 252 safe business operations.
D alone: 84 cases across three trials, including 54 delivered rejections with
zero consumption calls and persisted business changes, 18 reservation-first
serialized acceptances, six real rollback cases, and six concurrent/duplicate
cases. Semantic checks: 45 pass / 0 fail / 0 skip; smoke: 36 pass.

| Design | Cell pass/fail | Interactive errors | Timeouts | HTTP500 | Sync errors | Conflicts | Retries |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Baseline | 58/2 | 75 | 58 | 17 | 0 | 505559 | 505486 |
| Canonical update then read | 58/2 | 66 | 53 | 13 | 0 | 549295 | 549231 |
| D returned document | 58/2 | 66 | 55 | 11 | 0 | 519850 | 519786 |

Every failed cell is physical, 100 concurrent distinct orders, one merchant,
in trials 1 and 2. The baseline also overloads; failures are not attributed to
D alone. All cells reached quiescence; no partial stock/piece-consumption state
was observed, but failed cells do contain acknowledged-transition shortfalls.
Do not describe these as zero overall violations. Deadline-capped p95 near90s
is not equivalence or eventual completion time.

The fusion works at the command level. For 100 different merchants, physical
median command counts per request are 28/29/28 (baseline/C/D); virtual 12/13/12.
Physical observed transaction-attempt p50 medians are1501.05/1551.62/1498.29ms;
virtual635.56/703.94/635.03ms. These attempt metrics include retries/aborts,
not only successful transactions. CPU seconds across measured cells are
777.13/833.45/774.40; instrumentation is included.

D hot-physical100 p95 improves versus C in each paired trial by8.75%,8.15%,
14.18%. Versus baseline the same pairs are+0.98%,-6.50%,+6.05%, not a uniform
improvement. Other paired regressions remain:

- Same merchant, virtual50different orders: D p95 versus baseline
  +11.83%,+3.16%,+15.71% (4.816→5.385s;8.645→8.918s;8.835→10.223s).
- Same merchant, virtual hot100: sync p95 versus baseline
  +7.82%,+20.59%,+20.03% (116.18→125.27ms;218.21→263.15ms;213.22→255.93ms).

519819 of D's519850 conflicts occurred on the existing merchant owner document,
31 on canonical order, zero on stock. No new merchant-wide writer lock was
introduced, and D does not remove the existing contention bottleneck. This
measured distribution supports attribution, not a full production capacity RCA.

Full evidence: [D-RESULT.json](evidence/D-RESULT.json),
[comparison table](evidence/d-comparison.csv),
[within-trial paired comparisons](evidence/d-paired.csv).
The report links every full raw artifact and its GitHub-reported digest.
All failures/timeouts remain in the artifacts; no success-only sampling.
Mongo I/O is unavailable, not zero. Client output I/O includes large evidence
files and must not be interpreted as application write efficiency.

Decision: correctness is demonstrated for the tested canonical DTO contract,
but no-material-regression and high-load acceptance are not established.
PR-B stays BLOCKED. No runtime implementation, merge, prepare, prepublish,
deploy, production test, backfill or customer data change occurred.

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

## Preserved setup failure

Run 37936069340 at 4b512204b761f69f3adf84cb3f523af854c0839d passed
safety and semantic checks on all three runners, then failed BEFORE performance
because the new polling-control fixture database name was 64 characters (Mongo
limit 63). Its failure/artifacts remain retained. Only the disposable database
prefix was shortened. A trace label was also corrected from "committed after"
to "observed after": the observation alone does not prove commit ordering;
the explicit interleave barriers do. No design/retry/deadline changes.
