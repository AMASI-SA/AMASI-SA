# Final reviewed-B CI evidence

Task: `OPERATIONAL-HOME-LOADING-RECOVERY-20261122`.
GitHub evidence inspected read-only on 2026-11-22; final commit-scoped snapshot
at **12:19:01 UTC**. [PR #2059](https://github.com/AMASI-SA/AMASI-SA/pull/2059).

## Immutable candidate and normal merge

| Identity | Value |
| --- | --- |
| Source A2 | `d396049f8fedf72c394169a451d45b227ad52376` |
| Reviewed intent B | `8e5d0113be0daabe0989037c67315f2fc3d46574` |
| Normal merge M5 | `c302943e330bcdd534a4a5697b8ef813dfb7cb6e` |
| B and M5 tree | `2eaeb022fa3cfc087026302158b35bcbc33ac990` |
| Runtime release ID | `rg5-aceb392af7809d9099ba92b27d4bb3d802999148c91013a5bba1cca5ad5f6c8e` |
| Frontend artifact tree SHA-256 | `1aa92ab8ee890e1deb23f3c6742d75bb5748401b73ac45eb17e7d86aee378a88` |

The evidence worktree independently confirmed M5's parents are published M4
`1c4008e315c35ec4419e76400a9faddb94b9d5a2` and B. `git diff --name-only B M5`
returned no paths. The CI evidence below is the completed PR evidence for B;
it does not claim that a separate post-merge M5 workflow executed.

## Six release executions: all successful

[Mezan Release Readiness run 45020709490](https://github.com/AMASI-SA/AMASI-SA/actions/runs/45020709490).
All six jobs checked out literal B and actually executed.

| Execution | Job | Result |
| --- | --- | --- |
| Backend preflight | [137202742435](https://github.com/AMASI-SA/AMASI-SA/actions/runs/45020709490/job/137202742435) | 156 tests, OK |
| Host Node 24.19.0 engine and dispatcher contract | [137202742276](https://github.com/AMASI-SA/AMASI-SA/actions/runs/45020709490/job/137202742276) | 10 passed; zero failed/skipped |
| Host Node 20.20.2 engine and dispatcher contract | [137202742596](https://github.com/AMASI-SA/AMASI-SA/actions/runs/45020709490/job/137202742596) | 10 passed; zero failed/skipped |
| Frontend build | [137202742576](https://github.com/AMASI-SA/AMASI-SA/actions/runs/45020709490/job/137202742576) | Reviewed-intent classification, reproducible build and verification passed |
| Actual Emergent adapter, Host Node 24.19.0 | [137203043117](https://github.com/AMASI-SA/AMASI-SA/actions/runs/45020709490/job/137203043117) | Adapter invocation, package/identity checks and served JSON metadata passed |
| Actual Emergent adapter, Host Node 20.20.2 | [137203043146](https://github.com/AMASI-SA/AMASI-SA/actions/runs/45020709490/job/137203043146) | Adapter invocation, package/identity checks and served JSON metadata passed |

Both adapter logs verified A2, the runtime release ID and frontend artifact
digest above. Retained artifacts are bound to exact B:

| Host | Generated outputs | Clean-clone evidence |
| --- | --- | --- |
| Node 24.19.0 | [14754290105](https://github.com/AMASI-SA/AMASI-SA/actions/runs/45020709490/artifacts/14754290105) | [14753304396](https://github.com/AMASI-SA/AMASI-SA/actions/runs/45020709490/artifacts/14753304396) |
| Node 20.20.2 | [14754170599](https://github.com/AMASI-SA/AMASI-SA/actions/runs/45020709490/artifacts/14754170599) | [14754245427](https://github.com/AMASI-SA/AMASI-SA/actions/runs/45020709490/artifacts/14754245427) |

The PR's push-only **Emergent manual redeploy required** handoff job was
intentionally skipped and is not counted among these six executions.

## Regression gates closed on exact B

### Operational balances

[Run 45020709494](https://github.com/AMASI-SA/AMASI-SA/actions/runs/45020709494),
[backend job 137202742290](https://github.com/AMASI-SA/AMASI-SA/actions/runs/45020709494/job/137202742290):
**6,265 passed plus 90 subtests** at 12:16:05 UTC, followed by **16 passed plus
18 subtests** for shipping preservation. The executed JUnit guard required
`test_operational_catalog_read_scope >= 21` and
`test_order_batch_cpu_fairness >= 7`, then rejected any skipped, failed or
errored testcase. All assertions passed.
[Backend report artifact 14754420440](https://github.com/AMASI-SA/AMASI-SA/actions/runs/45020709494/artifacts/14754420440).

The [frontend job](https://github.com/AMASI-SA/AMASI-SA/actions/runs/45020709494/job/137202742216)
passed **90 suites / 1,426 tests**, explicitly including
`ApprovedFinance.loading.test.jsx`, and passed its no-skips guard. The
[browser/mobile/visibility acceptance job](https://github.com/AMASI-SA/AMASI-SA/actions/runs/45020709494/job/137202742185)
passed with synthetic backend fixtures. The
[canonical-worker job](https://github.com/AMASI-SA/AMASI-SA/actions/runs/45020709494/job/137202742092)
passed four tests. Browser fixture success is not a live Production check.

### The 41 locally skipped assembly cases executed in isolated Mongo CI

[Review Completion Resumable run 45020709533](https://github.com/AMASI-SA/AMASI-SA/actions/runs/45020709533),
[job 137202742557](https://github.com/AMASI-SA/AMASI-SA/actions/runs/45020709533/job/137202742557):
literal B checkout, disposable **MongoDB 8.0.12** replica set `reviewtest`, and
explicit loopback `MZ2_TEST_MONGO_URI` were verified in the log.

| Group | Passed cases | Passed subtests |
| --- | ---: | ---: |
| Preparation refresh recovery | 35 | 35 |
| Android operations | 348 | 59 |
| Local approval and retained provider contracts | 1,081 | 596 |
| Source snapshot and sync fences | 125 | 28 |
| Acceptance configuration guard | 1 | 0 |

The main group finished at 12:16:20 UTC. At **12:17:38 UTC**, the final executed
guard required **`test_review_local_assembly.LocalAssemblyTests >= 41`** and
confirmed zero skips/failures/errors for the three final reports. Preparation
recovery and Android operations passed their own zero-skip guards. This closes
the 41 locally skipped Mongo cases with actual isolated-Mongo execution.
[Report artifact 14754480162](https://github.com/AMASI-SA/AMASI-SA/actions/runs/45020709533/artifacts/14754480162).

## All matching PR workflows settled

The final commit-scoped query returned **16 runs: 15 successful and one
intentionally skipped**, with no failed, cancelled or pending run. Besides the
three critical runs above:

| Workflow | Evidence and scope |
| --- | --- |
| [Operational Stock Eligibility](https://github.com/AMASI-SA/AMASI-SA/actions/runs/45020709480) | Exact B; 5,525 passed plus 97 subtests; explicit zero-skips/failures/errors guard passed at 12:17:52 UTC. |
| [Security Gate](https://github.com/AMASI-SA/AMASI-SA/actions/runs/45020709420) | Exact B; frontend 457 suites / 4,989 tests; backend security contracts, dependency audits, build and manifest checks passed. |
| [Supplier cost report performance](https://github.com/AMASI-SA/AMASI-SA/actions/runs/45020709427) | Exact B; 33 tests; synthetic 6,000-order / 6,000-history benchmark passed. |
| [MZ2 Track F native shipping](https://github.com/AMASI-SA/AMASI-SA/actions/runs/45020709423) | Exact B; 885 tests plus 549 subtests passed. |
| [G47 Focused Integration](https://github.com/AMASI-SA/AMASI-SA/actions/runs/45020709445) | Exact B; backend groups 238, 331, 58 and 436 passed with zero-skip guards; UI groups 145 and 320 passed. |
| [Qoyod Payment Freshness](https://github.com/AMASI-SA/AMASI-SA/actions/runs/45020709415) | Exact B; 164 tests passed. |
| [Fulfillment V2](https://github.com/AMASI-SA/AMASI-SA/actions/runs/45020709531) | Exact B; backend 508 tests plus 59 subtests, separate 35 unittest cases and 3 tests; frontend 312 plus 2 tests; build passed. |
| [Mobile Order Tracking](https://github.com/AMASI-SA/AMASI-SA/actions/runs/45020709439) | Exact B; 252 tests plus 61 subtests passed using real Mongo. |
| [CodeQL](https://github.com/AMASI-SA/AMASI-SA/actions/runs/45020709404) | Python and JavaScript/TypeScript analyses passed; checkout was synthetic PR merge `4c4b470`, associated with B, not literal B. |
| [MZ2 Employee Salary and Password](https://github.com/AMASI-SA/AMASI-SA/actions/runs/45020709497) | Successful guards enforced backend >=127 and frontend >=32 with no failed/skipped/pending cases; synthetic PR merge `4c4b470` checkout. Actual totals were retained in its artifact, not printed in the inspected log. |
| [Snapchat Direct Active Publication](https://github.com/AMASI-SA/AMASI-SA/actions/runs/45020709496) | Scope guard succeeded; publication test jobs intentionally skipped. |
| [Snapchat Settings Management V2](https://github.com/AMASI-SA/AMASI-SA/actions/runs/45020709513) | Scope guard succeeded; settings test jobs intentionally skipped. |
| [TikTok Creator Release Candidate](https://github.com/AMASI-SA/AMASI-SA/actions/runs/45020709436) | Intentionally skipped. |

Scoped skips are not counted as domain execution evidence. CodeQL and Employee
are successful checks associated with B but are not literal-B checkout claims.
Test groups overlap and their counts must not be summed into a unique-test
total. One Security backend compatibility case was deselected and is not
passing-test evidence. No concrete CI failure was observed.

## Actual-host rehearsal and publication handoff

The coordinator separately reported an actual hosting-terminal isolated
adapter rehearsal under **Host Node 24.19.0**, at B, with **exit 0 in 67.103
seconds**. It built A2 and matched the runtime and artifact identities above;
package evidence counted **1,290 Backend files and 1,758 Frontend files**, with
all isolated checks true. This host observation is coordinator-supplied evidence,
separate from the directly inspected GitHub logs.

At the coordinator's handoff for this document, the owner lease had been
prepared at **12:21:13 UTC**, and **one Emergent publication had been submitted
and was building**. A newer successful deployment, governed identity probes,
home/auth/health acceptance and lease closure remained **pending**. This record
does not claim live success or resolution of the production pressure.

Only this evidence document was added by its author. No CI rerun, commit,
push, browser, host or production action was performed for this documentation
task. The coordinator owns persistence and the subsequent production record.
