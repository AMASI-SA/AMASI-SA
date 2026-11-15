# Ready refresh recovery — work in progress

## Scope and current state

- Repository: `AMASI-SA/AMASI-SA`.
- Task branch: `codex/ready-reconciliation-followup-20261116`.
- Reviewed production base: `236a428364bc8163a684795ed1be87e237476f18`.
- Base tree: `6967da090ac1301ca38bc8bc179758d7eaa91976`.
- Production changed by this task: **no**. No lease, publish, business-state repair, Ready click, receipt, service completion or provider status write has occurred.
- This is a source-work checkpoint, not a release or acceptance result.

## Two different failure modes

1. A committed, fully routed preparation can retain a stale local review stage after provider readback or local continuation fails. The proposed fix retries the existing guarded, provider-read-only continuation after an explicit successful one-order refresh in Orders or Assembly. It does not add a scheduler or scan other orders.
2. The newly reported received garment has a current `in_progress` stage and complete allocation. Its Ready action is blocked by an unresolved source/approval comparison. Receipt evidence is present. Pure diagnostics prove several representation additions, but the complete immutable-source proof still fails on other fields. This patch must not clear that flag, weaken unknown-field guards, or claim this incident is repaired.

## Implementation boundaries

- Mobile Orders and Assembly share the explicit refresh seam; owner web Orders uses its explicit action route.
- The generic source refresh function and implicit detail/search/review callers remain unchanged.
- Only a successful, fresh, exact-order canonical refresh receipt can start the local continuation.
- Provider dispatch stays disabled. Approval, scope, committed routing, source material and live actor authorization remain guarded, including authorization inside the local transaction.
- A successful source refresh remains distinguishable from a pending preparation continuation.
- Synthetic fixtures only. No private source payload, customer information, credentials or live order fixture is included in this branch.

## Verified milestone

The unchanged mobile refresh first failed the regression expecting a returned guarded preparation result. After bounded wiring, the following focused command passed six unit contracts:

```sh
cd backend
PYTHON_DOTENV_DISABLED=1 PYTHONPATH=.:tests python -m unittest tests.test_preparation_refresh_recovery.ExplicitRefreshContractTests -v
```

The contracts cover exact refresh proof, source-success/pending separation, authorization denial and recovery read failure. These are not transaction acceptance evidence.

## Remaining acceptance and review

- Finish named real-Mongo integration regressions and negative cases.
- Review the final immutable patch independently.
- Run the focused existing CI workflow with MongoDB 8.0.12 replica-set transactions, exact PR head and zero skips.
- Local MongoDB 8.0.12 startup is blocked before `--version`: the official SHA256-verified binary requires `/sys/devices/system/cpu/possible`, which this execution environment does not expose. No server or database started; no system files were modified. This is a test-environment limitation, not a passing database gate.
- Do not merge or publish before the required gates and a separate concrete release are complete.

## Continuation

Issue #1006 has reached GitHub's comment limit. Use this task document and the task Draft PR body as the recoverable continuation record; record the exact checkpoint SHA and CI URLs there after each material checkpoint. The previous publication-evidence branch remains independent and must not be merged as application source.

Next safe action: complete the targeted tests and CI wiring, checkpoint the full patch, then inspect the exact-head CI result and independent review. Continue the garment source-proof diagnosis without production mutation.

## Known WIP review finding

Independent review identified a stale-success fast path after provider readback when preparation was already `in_progress/sent`. The frozen WIP checkpoint still contains that finding and its initial owner-route test fixture; a follow-up is in progress to revalidate the live scope and mount the exact core refresh route without unrelated workspace imports. Do not treat this checkpoint as accepted or releasable. No PR/CI acceptance or deployment has occurred.
