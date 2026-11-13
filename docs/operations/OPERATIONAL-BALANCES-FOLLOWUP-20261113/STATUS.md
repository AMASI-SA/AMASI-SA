# Operational balances live refresh follow-up — 2026-11-13

## Current state

The combined source is locally verified and independently reviewed. Exact-source
CI and a new reviewed v5 source/intent pair are still required. This source is
not yet merged or deployed. Fresh financial acceptance remains FAILED on the
previous live release; no synthetic result is treated as production acceptance.

## Repository and continuity

- Repository: `AMASI-SA/AMASI-SA`.
- Task branch: `codex/operational-live-refresh-followup-20261113`.
- Draft PR: https://github.com/AMASI-SA/AMASI-SA/pull/1906.
- Base Production: `8d25f7a6fa9a310b319b753c32457e70987f7c51`.
- Initial remote UI WIP: `eb0017ecdb10dc7ab17b27e748f2b0d86532b44b`.
- Root owns integration and the already-authorized release operation.
- The exact next source SHA is recorded in the PR continuation entry; this
  document cannot contain its own commit identity.
- Issue1006 has reached its comment limit. PR1902 and PR1906 are the linked
  continuation threads. No coordination marker exists; no board was created.

## Completed behavior

### Pending-order dialog

An idle row without valid confirmation metadata could display the busy label
because both target key and busy key were null. The display now requires a
non-null busy key. The order number and reason remain visible; opening the
modal does not call preview or confirmation. A real explicit request still
shows its busy label until completion. Eligibility, permissions and saved
proof semantics are unchanged.

### Bounded cost reads

- Do not query units when no matching plans exist: the previous query's
  `plan_id: {$in: []}` could only return an empty set.
- The remaining-bundle adapter explicitly opts into overlapping three
  independent reads: stored snapshots, lifecycle and plans.
- The authority permits this only for an exact raw `AsyncIOMotorDatabase`
  owned by the current `DemandRefreshContext`, with the same DB identity and
  owner, and after `require_connected()` succeeds.
- Default callers, all wrappers/session adapters, mismatched owner/DB and
  calls outside that context remain serial. Ordinary financial GET therefore
  benefits only from skipping the empty units query.
- Results and errors are awaited in the previous source order. Failure or
  cancellation cancels and joins all started children; repeated cancellation
  cannot detach them. At most three authority reads are active at once.
- The full order cohort, original source limits, financial holds, monetary
  rules, deadlines, authorization and explicit confirmation are preserved.
  No scheduler, polling, hidden loading or background refresh was added.

The proposed historical-only cohort filter was rejected. Actual-module
synthetic reproduction showed that a newer order's frozen bundle evidence can
prevent incorrectly pricing an older order from its profile. The old filter
removed that hold. `operational_open_cost_exposure.py` is unchanged; the
regression preserving the hold is retained in the new test suite.

## Source files

- `backend/order_cost_authority.py`
- `backend/operational_remaining_bundle_sources.py`
- `backend/tests/test_operational_balance_cost_read_scope.py`
- `frontend/src/pages/operational/PendingOrdersDialog.jsx`
- `frontend/src/pages/operational/PendingOrdersDialog.test.jsx`
- `scripts/testing/operational_refresh_stage_analysis/measure_cost_authority_reads.py`
- This status and `COST-READ-MEASUREMENT.jsonl`.

No manifest, lockfile, release guard or frozen prior release intent was changed.
All fixtures and measured monetary values are synthetic. No live customer,
order, party, financial data or credentials are included in this public record.

## Completed verification

Backend command (repository root, isolated repository-pinned test dependencies):

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend:backend/tests python -B -m pytest \
  --noconftest -q --tb=short --maxfail=3 \
  backend/tests/test_operational_balance_cost_read_scope.py \
  backend/tests/test_order_cost_authority.py \
  backend/tests/test_operational_open_cost_exposure.py \
  backend/tests/test_operational_balance_sources.py
```

Exit0: **191 passed in17.50s**, including16 new cases. They cover full-cohort
financial evidence, result parity, units with/without plans, all wrapper and
owner/DB boundaries, read limits, error precedence, disconnect and repeated
cancellation with no remaining tasks. Initial import failures from missing
local test dependencies were resolved within the task venv before this pass;
no test expectations were weakened.

Frontend command:

```bash
CI=true python -B scripts/frontend_release_toolchain.py exec -- \
  yarn --cwd frontend test --watch=false --runInBand --resetMocks=false \
  --runTestsByPath src/pages/operational/PendingOrdersDialog.test.jsx
```

Exit0: **33 tests /1 suite passed**, no skips/failures; Jest8.887s. The pinned
Node22.23.2/Yarn1.22.22 bootstrap and single frozen-lockfile install passed.
The three new cases cover absent/malformed metadata and genuine in-flight
confirmation. The original defect was established live and in source; no
pre-fix automated red run was claimed. Package and lockfile diffs are empty.

Root reviewed the combined application diff and `git diff --check` passed.
An independent reviewer signed off the exact backend hashes below, after
checking context gates, serial fallbacks, ordering, cancellation/join and the
unchanged full cohort. That was an independent source review, not a second
execution of the191 tests or a production performance claim.

| File | SHA256 |
|---|---|
| order_cost_authority.py | `11e351f92bc1517ae0aa7c3d4fed168c038f8222cfde9a8a263c1cf38c6c60f7` |
| operational_remaining_bundle_sources.py | `911ebd5bce96a6ff05f12f2d3a8992e58c9ec396868a3f0fbfad8501ec7be2bf` |
| test_operational_balance_cost_read_scope.py | `30a167285bc8be479ca390650d7fed5ac7764dd7b27103d6b232847b313b3a19` |
| PendingOrdersDialog.jsx | `aeb6d1faa6aaf360f9ec1b1700e68e6382024de7551be473a174fadc7441a831` |
| PendingOrdersDialog.test.jsx | `6a723434f4f7a9e35ec33f9c48c7bde60562a80a8197963d1e3c22e50a536843` |

## Reproducible synthetic measurement

The tracked measurement script runs the actual `run_visible_refresh` and
`collect_sources` modules against in-memory DB boundaries, with10ms artificial
cursor latency. It makes no external DB call. Root ran the saved script
successfully and preserved its exact JSONL output alongside this document.

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:backend:backend/tests python -B \
  scripts/testing/operational_refresh_stage_analysis/measure_cost_authority_reads.py
```

| Orders | Source reads before/after | Authority peak before/after | Original measured ms before/after | Root reproduction ms before/after |
|---:|---:|---:|---:|---:|
| 1,000 |50 /46|1 /3|1185.941 /860.838|1908.545 /1183.308|
| 5,000 |114 /94|1 /3|3304.317 /2343.424|3537.936 /3155.214|

Both runs assert exact financial/source result equality, unchanged inputs,
unchanged2 lease operations and0 remaining tasks. The result digests match
between runs. Timing varies with the local environment; the deterministic
read-count reduction and parity are the mechanism evidence. This is not a
measurement of production latency or proof that its timeout is resolved.

## Live evidence and previous release

PR1902 source `1f8f1d673854f594086a6e87dec6c98633da0061`, deployment
`b64121c265d8d6e158df0d9fb88debc52c95317d`, is officially verified live.
The original operator completed the official verification2026-11-13 at
17:02:16UTC: exit0, three full passes,582 frontend requests per pass.
Subsequent official status returned `active:false`. Platform completion is
Nov13,18:48Riyadh, version0cdd01a. Do not republish that frozen pair.

The authenticated report displays saved balances and pending-order modals with
working pagination. Its initial visible refresh failed with a source-read
message; two manual diagnostic attempts used the refresh-timeout fallback.
A saved financial GET was observed status200 with duration31839.360ms.

A fourth total attempt was justified after Management returned a newer log
window. It started17:18:00.027UTC and remained visibly open; by17:20:20UTC it
again showed the refresh-timeout fallback. Log retrieval then remained cached
at17:17:23 despite navigation/reload, so no corresponding phase timing was
captured. Do not infer a single production bottleneck. Do not repeat blind
refreshes before a concrete change. No real confirmation, settlement or payout
was clicked.

Prior release evidence remains on documentation-only branch
`codex/operational-delivery-release-handoff-20261113` at
`e1eb7af40d5604a77b93f92f8dfaa071c8b660d4`, linked from PR1902.

## Next safe action

1. Preserve the combined source checkpoint on this task branch and inspect all
   exact-source CI results, including the new16 cases.
2. Recheck current Production. Freeze a new reviewed intent-only commit after
   the clean governed build; do not squash/rebase or add source after freezing.
3. Complete exact intent/merge gates and clean-clone rehearsals. Read fresh
   operator guard/coordination, HEAD and tracked/untracked state before any
   operator mutation. The prior closed lease must not be reused.
4. Use the owner's existing upload/merge/publish authorization and protocolv5.
   Require a newer explicit platform completion and official full verification.
5. Inspect the live report without real financial confirmations. Financial
   acceptance requires a fresh successful report, not a dated fallback.

New task Production changed: **NO**. No new operator source mutation, lease or
Publish action has occurred at this source checkpoint. Preserve unrelated
Production8d25 work, operator untracked files and unrelated terminals.
