# Operational source-read repair — 14 November 2026

## Current status

The safe diagnostic stage is implemented and locally verified. It repairs the loss of the source/cause in the visible warning, but **does not yet repair the underlying production source-read failure**. No release intent, merge or publication by this task exists at this checkpoint. The user explicitly authorized fixing, merging and publishing. The accepted behavior remains page-owned computation: refresh only while visible and cancel/join work on hide or exit. No background calculation, polling, financial confirmation, settlement or payout is authorized as a test.

## Continuity and current base

- Task branch: `codex/operational-source-read-repair-20261114`.
- Draft PR: https://github.com/AMASI-SA/AMASI-SA/pull/1913.
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

## Next safe action

Run exact-source CI and freeze a new diagnostic intent only after it passes. Any further proven existing authenticated GET count may supply narrower evidence without a new refresh; do not invent endpoints or bypass browser restrictions. If it does not identify the blocker, publish the diagnostic stage through the full guarded protocol after the concurrent release closes, reproduce one visible attempt, and fix the demonstrated cause. An instrumentation-only release is not completion.

Keep the existing owner scope, full financial evidence and cancellation guarantees. Add cause-specific regression coverage and obtain independent review. Freeze a new source A and intent-only B under protocol v5, preserve both with normal merge, then publish through the original adapter and complete official verification plus a fresh visible-page acceptance check.

Production changed by this task: **NO**. No customer data, account identifiers, order identifiers, financial amounts or credentials are included in this record.
