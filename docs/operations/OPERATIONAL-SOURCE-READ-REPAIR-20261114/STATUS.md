# Operational source-read repair — 14 November 2026

## Current status

The safe diagnostic stage is implemented, independently reviewed, fully verified on A/B/M in CI, and merged as PR #1913. It repairs the loss of the source/cause in the visible warning, but **does not yet repair the underlying production source-read failure**. The original operator checkout is at the exact reviewed merge M; no publication or lease by this task has occurred. The first operator package build stopped safely because preexisting ignored Python bytecode was present. The generated cache was reversibly preserved outside /app and the actual configured adapter retry succeeded; its runtime identity matches the reviewed intent exactly. This documentation-only handoff branch must not be merged or published. The user explicitly authorized fixing, merging and publishing. The accepted behavior remains page-owned computation: refresh only while visible and cancel/join work on hide or exit. No background calculation, polling, financial confirmation, settlement or payout is authorized as a test.

## Continuity and current base

- Task branch: `codex/operational-source-read-repair-20261114`.
- Merged PR: https://github.com/AMASI-SA/AMASI-SA/pull/1913.
- Initial remote checkpoint: `a1497e7ff7e2cdec60be4e3c34d516a1441d2346`.
- Production base read: `304e8793a15fd3269fd6f6d4cfba903d1760e285` (PR #1912), including the prior campaign changes from #1911.
- Previous balances release #1906 is already merged and officially verified. Its later live application check failed; do not republish that source/intent.
- Previous handoff: [STATUS](https://github.com/AMASI-SA/AMASI-SA/blob/a496614da4a569817fd1e959b25989ae62cbe31b/docs/operations/OPERATIONAL-BALANCES-FOLLOWUP-20261113/STATUS.md).
- Issue #1006 has reached its comment cap; #1902/#1906 are balances continuation threads.
- No coordinator marker or repository trigger exists. Root owns this isolated branch and integration. One scoped child implemented the three diagnostic files; a separate child independently reviews them. Root owns all release operations.

## Proved application result and remaining gap

At 2026-11-13T22:30:18.040Z, the visible financial page settled with the source-read failure warning and retained `as_of=12 November 2026 01:07` (UTC+3). Its newer report-render timestamp did not demonstrate a successful calculation. The exact message maps to the SourceReadError handler in `source_collection`, inferred from source rather than a captured HTTP response. Current code preserves cause/collection in HTTP409 details, but the UI discards these fields and the failure exits before state/audit persistence. The exact failing collection and cause remain unproved.

Independent current-source review maps this error to the first 32-source batch. It reads owner-only records with an exact post-read owner check, a 20,000-row ceiling and a 15-second per-read deadline. There is no evidence yet justifying changing those safeguards or dropping historical orders. Earlier orders can still generate post-opening delivery, custody and shipping evidence.

## Work completed in this continuation

- Read latest repository instructions, previous handoff and relevant release records.
- Fetched production and created a clean isolated task branch from current base.
- Restored browser/operator UI and verified signed-in account role using ordinary authenticated GET; no cookies/tokens were extracted.
- Fresh original `/app` guard returned `active:false`; `/app` HEAD was `304e8793a15fd3269fd6f6d4cfba903d1760e285`, with empty tracked/index diffs. This is an observation, not a retained lease or authorization to interfere with another task.
- A bounded read-only diagnostic on the configured operator DB verified the owner role, but found no matching aggregate. It stopped before all source counts (checked=0), without reads of financial payloads or writes. This DB cannot establish the production cause. No connection strings, environment values, cookies or tokens were exposed or copied.
- The official database viewer opened through the existing Emergent account. Its Preview view is available; its Production view requires separate connection configuration. No production connection credential was extracted and no production DB query was run there.
- One ordinary authenticated `GET /api/orders?limit=1&page=1`, after source review proved its exact owner-only predicate and absence of side effects, returned a total below the existing 20,000-row limit. Only count/pagination metadata was read into the diagnostic output; no order payload was printed. This rules out a current orders row-cap breach, not an orders read timeout or a failure in the other 31 sources. The business count is not included in this public record.
- Another Ready release owner began publication at 14 November 02:39 UTC+3, observed in the normal Emergent management UI. Do not change its source, artifacts, lease or active work. Fresh status and production head are required before any later operator mutation.

## Diagnostic change

The existing SourceReadError catch retains HTTP409 and the same machine fields for all five known causes and 32 first-batch sources. A fixed allowlist maps them to distinct Arabic source/reason text in the existing `message` field, which the deployed frontend already displays. The old saved-snapshot warning remains explicit. Unknown values become fixed `unknown` markers; arbitrary objects/string subclasses are never formatted or echoed.

One warning event records only a fixed stage/outcome and the allowlisted cause/collection. No exception text, stack, owner, order, monetary value, query or credential is logged. No additional reads, writes, timers, retries, calculations or frontend changes are introduced. The existing source cap, deadlines, owner checks, error status, preserved snapshot/audit and cancellation behavior remain unchanged.

Files: `backend/operational_balance_service.py`, `backend/operational_source_diagnostics.py`, `backend/tests/test_operational_balance_source_diagnostics.py`. The test name deliberately matches the existing Operational Balance CI glob; no workflow or expected baseline was weakened.

## Local verification

- Baseline at the initial checkpoint: 92 passed in 6.64 seconds.
- A new refresh-level regression ran against the original handler and failed for the intended reason: source visible, reason visible, diagnostic log count were `(False, False, 0)` instead of `(True, True, 1)`. HTTP409, saved snapshot/audit and no writes already passed.
- Corrected integrated run: **271 passed in 6.78 seconds, exit0**, including 179 diagnostic cases plus the 92 read/runtime/demand/route/stage cases. Tests cover known causes/sources, untrusted values/log injection, exact HTTP409 fields, preserved saved aggregate/audit, one safe log and cancellation without conversion or writes.
- Test dependencies are isolated and constrained by the backend runtime lock; Python3.12.14 locally. The exact-source prescribed CI uses Python3.11 and must still pass. Initial missing local dependencies were installed to their locked versions; no product failures were dismissed.
- `git diff --check` passed. Root and a separate independent reviewer approved the complete diagnostic application diff. The reviewer intercepted the actual first-batch call before DB I/O and proved all 32 source keys match exactly, checked the five causes and safe formatting boundary, and independently ran the seven refresh/preservation/hostile-field/cancellation cases (7 passed in 1.54 seconds).

Reproduction from repository root:

```bash
PYTHONPATH=backend:backend/tests PYTHON_DOTENV_DISABLED=1 \
/workspace/scratch/019fa8a18b3d/mezan-operational-test-venv/bin/python -B -m pytest \
  --noconftest -q --asyncio-mode=auto --tb=short \
  backend/tests/test_operational_balance_source_diagnostics.py \
  backend/tests/test_operational_read_runtime.py \
  backend/tests/test_operational_balance_sources.py \
  backend/tests/test_operational_balance_demand_refresh.py \
  backend/tests/test_operational_balance_demand_refresh_routes.py \
  backend/tests/test_operational_balance_refresh_stage_timing.py
```

| File | SHA256 |
| --- | --- |
| operational_balance_service.py | `94419f6cd928f3df14a44e8ad2faf3d7b0d2e4f2b1e8dfea90d5676c9b205e68` |
| operational_source_diagnostics.py | `8ede8a7cb44cbc04c68b0a22a99f0cc0d0bacc7295fe9925c064b47526615fa4` |
| test_operational_balance_source_diagnostics.py | `51e400012e491dd3c6bc6578932f025c5ddb41a8cf0d3359ded48d28c2ce9210` |

## Release checkpoint — 2026-11-14 UTC

- Source A: `54184a6c3725067eda2e9ac56345f578425cf3f0`; tree `969894a74194268f968754acaa52cbbdac816220`.
- Intent-only B: `b2feb7d4b2f8bbd58e28644733a09205b2da4a05`.
- Normal merge M: `6012ceb480bf9dc0f51ec6cc64c054e5e0db3846`; B/M tree `2b6e4d1da108350b3d0f8637000e7de09fb3bd7b`.
- Merge parents in order: prior production `304e8793a15fd3269fd6f6d4cfba903d1760e285`, then B. Merged 2026-11-14T00:05:32Z; remote production ref verified M.
- Runtime identity: `rg5-51a27cab964259844260959047effc778bbe1aa204c5da13133af9e0628fcaaa`.
- Intent SHA256: `43f7acd0c0c2217087029cec6ca656d64bddd6f5454f1a3fb2f1a1b46e6ad315`; 1,301,645 bytes; Git blob `0d12084f8113345bc311e2570a564896c3b1affc`.
- A: 27 checks, 21 success and 6 expected scope/source-role skips, no pending/failure. B: 29 checks, 23 success and 6 expected skips. Both B host20/host24 clean-clone adapter rehearsals actually ran and passed.
- Exact A and B Operational Balance JUnit artifacts each contain 3,981 cases, including all 179 new cases, with zero failures/errors/skips; embedded SHA/tree matched. B artifact14087202774 ZIP SHA256 `5f84d9469dae00c3d0d05eb786d5980f2e9fe48a208ac85456ef461baf2f6d16`.
- A frontend verification: 721 tests/51 suites; worker4/4; browser9/9. These are synthetic checks, not live financial acceptance.
- M independently checked at 00:14:03 UTC: all 11 checks success, no pending/skipped/failure. Readiness43531799950, Security43531799982, CodeQL43531799971 all success.
- Candidate artifact14085749847 from A Readiness43529628224 downloaded and hash-checked; ZIP SHA256 `df20ad159b2fa557dcce9d7ac3a0a56fa6e8410a266617b8597ae1fb1ba36ed5`. Official candidate validation exit0 in a fresh clean clone; official B intent/Git validation exit0.

## Original operator state and safe build recovery

The previous Ready release lease closed without intervention by this task. Fresh official guard checks returned exactly `{"active":false}` before each operator mutation. Original `/app` was fast-forwarded from the previous production head to M; tracked/index clean and all 120 preexisting untracked files byte-for-byte unchanged. Preservation manifest SHA256: `fc1aade13ea5358fed8ce97e532a7d4a600674ebce99c60ad5417b0d9c380dd8`. Unsaved editor content and unrelated files were preserved.

The actual configured `yarn build` in `/app/frontend`, with the reviewed public backend URL and Python bytecode writing disabled, exited1 in1.52 seconds before bootstrap. Sanitized adapter error: `release source manifests are invalid: Backend runtime source contains prepackaged bytecode: __pycache__`. Read-only inspection found exactly one ignored `backend/__pycache__` directory (two files,120563 bytes); no standalone bytecode or other caches in backend/scripts/workflow/action scan roots. No application source failure or candidate-identity mismatch was reported.

Failed-build evidence remains at `/tmp/mezan-pr1913-6012ceb4-build.log`, `.err`, and `-result.json` on the original operator; the substantive failure and identities are durably recorded here. Next recover by preserving these generated ignored cache files outside the deployment source roots, checking exact before/after hashes, then running a new logged build with no source/intent changes. Never delete or overwrite unrelated files, disable the manifest check, use fake CI markers, or modify the frozen A/B/M.

This handoff is on `codex/operational-source-release-handoff-20261114`, branched from M. Its commit SHA is recorded in the PR continuation comment; do not deploy the documentation branch.

## Next safe action

1. Completed: ignored cache was preserved outside /app; exact package adapter on M succeeded, with Python bytecode writing disabled.
2. Completed: actual adapter exit0, exact intended A/runtime identity and clean tracked files verified. All 120 original untracked files retain their original bytes; one platform metadata file has an append-only addition, preserved in place. Fresh guard inactive and remote production head M rechecked. Prepare a unique own lease.
3. Run own `prepare`, then `prepublish` immediately before one authorized normal Emergent publish. Preserve a newer explicit platform success and run the full official three-probe verification. Require exit0 and inactive guard afterward.
4. Reproduce one visible financial-page attempt to obtain the source/cause. Implement the demonstrated root fix with regression tests and independent review, then new A/B/merge/release and fresh successful calculation timestamp. Diagnostic instrumentation alone is not completion.

Keep the existing owner scope, full financial evidence and cancellation guarantees. No background calculation or financial mutation as a test. No credentials, customer identifiers or financial payloads are included in this record.

Production source branch changed by this task: **YES**, normal M merge above. Production runtime changed by this task: **NO** at this checkpoint; no own prepare/prepublish/Publish/verify.

## Build recovery passed

The observed ignored backend cache (two files,120563 bytes) was moved to `/tmp/mezan-pr1913-preserved-bytecode-6012ceb4/backend/__pycache__`; per-file SHA256, size and mode matched after the move. No file was deleted and no reviewed source/intent was changed.

The real `cd /app/frontend; yarn build` adapter returned0 and completed in61.64s. The wrapper then stopped at its stricter whole-untracked-manifest assertion, because `.emergent/stripped_env_names` had grown by86 bytes. Subsequent bounded audit proved the original45236-byte prefix still has exactly the original SHA256; all119 other files are unchanged, no files were added/removed, tracked/index clean, and the generated runtime identity equals the complete tracked intent runtime identity. The sidecar was left untouched. Adapter exit0 is supported by the wrapper entering the post-returncode==0 branch and Yarn's successful completion; wrapper exit1 is retained honestly, not described as a successful wrapper.

Evidence: `/tmp/mezan-pr1913-6012ceb4-build-retry1.log`, `.err`, and `-audit.json` on the original operator. Fresh official guard returned `active:false` after the audit. No own lease or Publish yet. Next: own prepare/prepublish, one normal authorized Publish, explicit new platform success, then full official live verification.
