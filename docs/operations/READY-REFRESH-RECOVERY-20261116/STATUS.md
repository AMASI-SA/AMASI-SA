# Ready refresh recovery — fresh source candidate on current release base

## Scope and current state

- Repository: `AMASI-SA/AMASI-SA`.
- Task branch: `codex/ready-refresh-recovery-release-20261116`.
- Historical checkpoint branch: `codex/ready-reconciliation-followup-20261116` (Draft PR #1952, retained; not release-admissible).
- Current production Git base: `2589f440106a7c5d463ee03eac3617d113319673` (PR #1951).
- Current base tree: `d91e281aa9fc7e02ddab1a36989dc6c3bf0b68ec`.
- Original reviewed base: `236a428364bc8163a684795ed1be87e237476f18`.
- Production changed by this task: **no**. No lease, publish, business-state repair, Ready click, receipt, service completion or provider status write has occurred.
- First remote WIP checkpoint: `0037fb2beee3738ba878ca09e02fee9be37f0ee2`.
- This is a source candidate awaiting its own exact-head acceptance and release proof. Historical passing integration results are recorded below; no production publication of this task is claimed.

## Two different failure modes

1. A committed, fully routed preparation can retain a stale local review stage after provider readback or local continuation fails. The proposed fix retries the existing guarded, provider-read-only continuation after an explicit successful one-order refresh in Orders or Assembly. It does not add a scheduler or scan other orders.
2. The newly reported received garment has a current `in_progress` stage and complete allocation. Its Ready action is blocked by an unresolved source/approval comparison. Receipt evidence is present. Verified historical captures now reconstruct the entire approved raw source with an exact hash match. Current-versus-approved representation proof remains separate and incomplete. This patch must not clear that flag, weaken unknown-field guards, or claim this incident is repaired.

## Implementation boundaries

- Mobile Orders and Assembly share the explicit refresh seam; owner web Orders uses its explicit action route.
- The generic source refresh function and implicit detail/search/review callers remain unchanged.
- Only a successful, fresh, exact-order canonical refresh receipt can start the local continuation.
- Provider dispatch stays disabled. Approval, scope, committed routing, source material and live actor authorization remain guarded, including authorization inside the local transaction.
- A successful source refresh remains distinguishable from a pending preparation continuation.
- Synthetic fixtures only. No private source payload, customer information, credentials or live order fixture is included in this branch.

## Verified milestone

The unchanged mobile refresh first failed the regression expecting a returned guarded preparation result. After bounded wiring, the following focused command passed all seven unit contracts in an independent root rerun:

```sh
cd backend
PYTHON_DOTENV_DISABLED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:tests python -m unittest test_preparation_refresh_recovery.ExplicitRefreshContractTests -v
```

The contracts cover exact refresh proof, source-success/pending separation, authorization denial, unavailable/demoted owner access and recovery read failure. These are not transaction acceptance evidence.

Independent review found a pre-existing stale-success branch newly reached by explicit refresh: after provider readback, an already-confirmed workflow returned success without checking a concurrent cancellation or completion. The candidate revalidates that no-op path inside `operational_owner`, including current authorization, approval identity, revision, source, coverage and final stage/status. Two additional integration cases cover source and workflow races. A repeated successful refresh must preserve every business document after the source refresh; only the owner fence may advance.

## Remaining acceptance and review

- Original candidate `bbdca0dd2b3fcc8ceac3614f086e134776f9a21c` passed all 13 exact-head workflows. Review Completion Resumable run `43773103462` / job `132993189248` proved MongoDB 8.0.12 and 35 focused cases, 329 Android cases, 828 review/provider cases, 124 source-fence cases and one conflict guard, all without skipped/failure/error results.
- Production Git advanced during release preparation. PR #1951 changed 17 paths, including its release intent, with no overlap with this task's seven paths. Its separate publication remains coordinated by its owner; Git merge and original workspace HEAD are not proof of live publication.
- The attempted merge successor `38ea76b1a8af4c244f4c494a73a120ce2410b56c` failed Readiness run `43774806987` at candidate source classification. The unchanged guard rejected an intent change in full merge history, despite matching final intent bytes. Its checkpoint is retained for audit and must not be frozen or published.
- This fresh candidate has only the current production base as its parent, applies the same six runtime/test/workflow blobs plus this status record, and inherits the base intent unchanged. It does not weaken the release guard, rewrite historical checkpoints, or reuse a frozen intent.
- Repeat exact-head CI and produce a fresh candidate intent before intent-only B. No historical A/B proof or shared lease may be reused.
- Independent runtime review accepted the final stale-success correction; no remaining concrete runtime/auth/scope/provider-write finding was reported. This does not replace transaction execution.
- Run the focused existing CI workflow with MongoDB 8.0.12 replica-set transactions, exact PR head and zero skips.
- Local MongoDB 8.0.12 startup is blocked before `--version`: the official SHA256-verified binary requires `/sys/devices/system/cpu/possible`, which this execution environment does not expose. No server or database started; no system files were modified. This is a test-environment limitation, not a passing database gate.
- Do not merge or publish before the required gates and a separate concrete release are complete.

## Continuation

Issue #1006 has reached GitHub's comment limit. Use this task document and the task Draft PR body as the recoverable continuation record; record the exact checkpoint SHA and CI URLs there after each material checkpoint. The previous publication-evidence branch remains independent and must not be merged as application source.

Next safe action: open the fresh-source Draft PR, verify its sole parent and exact seven-path delta, and inspect its own exact-head CI/candidate-intent producer. A read-only original workspace observation on 2026-11-15 around 23:31 UTC showed Release Guard inactive, HEAD at the production Git base, and no tracked changes; this is historical coordination evidence, not a reusable lease or publication proof. Re-read the guard immediately before any rehearsal or original mutation and preserve the ongoing PR #1951 publication. Continue the separate garment-source proof work without forcing its current guard open.

