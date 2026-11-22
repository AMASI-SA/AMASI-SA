# CI coverage and reviewed-intent readiness

Readiness inspection began at clean source A
`e9f5f85d7a9a8a1198ef593d564c20c2144a20e9`, tree
`923c283b6393c1c87d0f2c511742f31a103ae1d2`, on 2026-11-22.
The coordinator authorized the four-line CI coverage change below and will
create a fresh source A2 before freezing an intent. No intent was edited by
this subagent; no CI run, commit, push or production action was started here.

## Concrete gap and minimal correction

The existing Operational Balance Backend command did not collect the two new
backend regression files: neither matches `test_operational_balance*.py`, and
neither was explicitly listed elsewhere. The operational frontend's recursive
`**/src/pages/operational/**/*.test.{js,jsx,ts,tsx}` selector already includes
`ApprovedFinance.loading.test.jsx`.

Only `.github/workflows/operational-balance-web.yml` was changed:

- Add `backend/tests/test_operational_catalog_read_scope.py` to the existing
  backend pytest command.
- Add `backend/tests/test_order_batch_cpu_fairness.py` to that command.
- Require at least 21 catalog cases and 7 order-batch cases in the existing
  JUnit completeness guard.

The existing global rejection of skipped, failed or errored test cases remains
unchanged. The workflow checks out and records the exact PR head SHA and tree.
It runs for PRs targeting `hotfix/prod-snap-meta-final`, without a path filter.

Validation: the complete workflow parsed successfully as YAML; its embedded
Python evidence guard parsed successfully; a static check confirmed both files
are present exactly once, both expected minimums are present, and the global
no-skips/failure/error guard remains. `git diff --check` returned exit 0.
The CI workflow itself has not been executed by this subagent.

## The 41 local Mongo skips have an existing CI gate

`.github/workflows/review-completion-resumable.yml` already covers them:

- Its PR path filter includes `backend/order_engine/repository.py`.
- Job `real-mongo-review` checks out the exact PR head.
- It starts a disposable MongoDB 8.0.12 replica set, `reviewtest`, on loopback
  port 27018 and sets `MZ2_TEST_MONGO_URI` to that fixture.
- Its main command explicitly includes `tests/test_review_local_assembly.py`.
- The JUnit guard requires at least 41
  `test_review_local_assembly.LocalAssemblyTests` cases and rejects every
  skipped, failed or errored case.

A successful, executed run of this job at the final reviewed B closes the
41 locally skipped tests. A skipped workflow or an older-head result does not.
The workflow also supports manual dispatch if the coordinator needs to invoke
the existing isolated suite explicitly.

## Other order-engine coverage

| Suite | Existing CI coverage for this change |
| --- | --- |
| `test_order_engine_repository.py` | Explicitly included by Fulfillment V2 / `backend-contract` and MZ2 Track F native shipping / `native-contract`; repository and `order_engine/**` path filters respectively match. |
| `test_order_engine_mapper.py` | Explicitly included by those same two jobs. |
| `test_order_engine_service.py` | No explicit workflow command or matching broad test-discovery command found. It passed in the controlled local affected-suite run; the new fairness suite also exercises the real service mapper. |
| `test_fulfillment_read_performance.py` | Included by Page and operational read performance, but that workflow's path filters do not match this candidate's changes. Its existing 205-order behavior is also exercised by the new fairness regression. |

Per coordinator instruction, no extra workflow trigger or broader test list was
added. The two existing suites without an automatic matching invocation can be
run at a chosen exact source with the established isolated environment:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 PYTHONPATH=backend:backend/tests /workspace/scratch/81f2839b514c/courier-test-env/bin/python -B -m pytest --noconftest -p no:cacheprovider -q --tb=short -o asyncio_default_fixture_loop_scope=function backend/tests/test_order_engine_service.py backend/tests/test_fulfillment_read_performance.py
```

This command was not rerun during the read-only readiness inspection. Both
files were included in the previously recorded 122-pass affected-suite run.

## Exact reviewed-B release workflow gates

`.github/workflows/mezan-production-release.yml` defines these required
release-readiness executions for reviewed B:

1. **Backend preflight**: compile all backend sources and run the five
   deployment adapter, release toolchain, frontend identity, release guard and
   runtime identity unit suites.
2. **Host Node 20.20.2 engine and dispatcher contract**.
3. **Host Node 24.19.0 engine and dispatcher contract**.
4. **Frontend build**: governed Node 22.23.2 / Yarn 1.22.22, build contract
   tests, verified reviewed-intent classification, reproducible A/B frontend
   build, artifact/proof verification and a clean tracked worktree.
5. **Emergent Host Node 20.20.2 clean-clone adapter rehearsal**.
6. **Emergent Host Node 24.19.0 clean-clone adapter rehearsal**.

The last two jobs run only when `reviewed_intent == 'true'`. Their absence on
an unreviewed source-A run is expected and does not establish deployability.
On reviewed B, both must actually execute and pass. They prove the intent-only
A2-to-B relationship, use a clean clone with generated inputs absent, emulate
the package adapter without Git metadata, verify package boundaries and exact
runtime/source/artifact identities, and test real JSON build metadata through
the governed runtime.

The **Emergent manual redeploy required** job runs only on a production-branch
push and depends on those readiness jobs; it records a handoff, not a deployment.
These are the gates defined by the repository source. GitHub branch-protection
requirements and live check conclusions were not queried by this subagent.

The domain evidence to inspect at final B includes Operational Balance Backend
(with the new 21/7 minimums), Operational Balance Frontend (including the new
loading file), Review Completion Resumable real Mongo (zero skips), and the
matching fulfillment/native-shipping jobs. The coordinator must also inspect
every other required/triggered check on that exact B rather than reusing old-A
successes. Production acceptance remains a separate home/auth/health and
release-identity verification after an authorized successful deployment.
