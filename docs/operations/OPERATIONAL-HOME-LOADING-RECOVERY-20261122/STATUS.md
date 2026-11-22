# Operational home loading recovery — 22 November 2026

## Scope and current state

The owner reports that `https://mezansalla.com/operational-balances` remains on
the balances loading screen. Prior explicit authorization to complete and publish
this repair remains active. This is a new follow-up incident after the completed
PR #2058 publication; the prior publication must not be repeated as if unfinished.

Repository: `AMASI-SA/AMASI-SA`.
Task branch: `codex/operational-home-loading-recovery-20261122`.
Verified production base: `1c4008e315c35ec4419e76400a9faddb94b9d5a2`.
Prior deployed source: `a8e655aabbeaef64486a1a80f13551dc97dd9259`.
Prior release: `rg5-9d69963c0ee7f5c299595495519175af5074f703f4e318b82b870b0407554a49`.
Prior completed evidence: `73b2ec5ce57a48e496929d2a9d212afb2cba17a8`,
on `codex/operational-context-release-evidence-20261122`.
The new checkpoint's exact SHA is the containing commit, retrievable from this
task branch; subsequent checkpoints will record its full predecessor explicitly.

Production changed by this incident task: **no**.
New lease, publication, rollback, resource upgrade, financial/provider/sync/backfill
write: **none**. Emergent administration and code-terminal access only; no paid
Emergent agent chat.

## Fresh observations

Observed on 22 November 2026, approximately 11:16–11:29 UTC
(14:16–14:29 Asia/Riyadh):

- Cold live navigation reached the application's unavailable-session recovery
  screen before the finance route could mount. This is an availability signal,
  not evidence that credentials were rejected or that the user's session ended.
- Production branch remains the base above. Emergent's latest displayed completed
  publication is 22 November 06:10 Riyadh, consistent with the previous release.
- Emergent Monitoring displays severe resource pressure over its last-24-hours
  window: CPU 84% with 100% peak, memory 65% with 100% peak, error rate 13% with
  16% peak. These are platform display values, not per-request measurements.
- Production View Logs at 11:27:28–11:27:50 UTC show internal upstream timeouts
  for `GET /health`, recurring at five-second intervals. Application timing logs
  in the same window show health requests around 0.9–1.9 seconds, with one
  32-millisecond sample.
- Other concurrent, independently occurring requests are slow: assembly orders
  reads around 112–114 seconds, a delivery status write around 64 seconds, account
  summary reads around 16–26 seconds, and multiple ordinary delivery/event
  requests around 4–7 seconds. This investigation did not initiate those writes.
  These observations establish broader service pressure, not its specific cause.
- Two local-executor public liveness/readiness samples reached an eight-second
  transport timeout. Executor transport/proxy time is not a backend measurement.

The exact screenshot text comes from the initial workspace-context gate.
Its initial request is
`GET /api/operational-balances/workspace/context?ad_bank_fx=true&provider_positions=true&home_preview=true`.
This route already uses the scoped current financial-state reader and does not
read historical audit-page bodies. PR #2058 repaired the distinct legacy context
route; repeating that change would not address this new home path.

## Source investigation and bounded repair plan

Read-only frontend review identified recoverability gaps in this context gate:
the default visible-read lifecycle has no overall deadline while waiting for a
prior physical request; hidden/offline deferral and an invalid authentication
gate may display the same loader with no active request. Normal Axios rejection
already produces an error. These gaps are confirmed in source but not yet the
demonstrated trigger in production.

A narrowly scoped frontend follow-up is being prepared using the existing
single-attempt policy, preserving identity checks, physical-request serialization,
and explicit retry after cancellation/offline interruption. Other balance reads
retain their current default behavior. It will require focused red/green tests.

The current financial report still decodes/copies current state and builds full
provider detail before compact home cards. Catalog queries are largely serial.
Cancellation/deadline coverage has gaps in those helpers. These are investigation
candidates, not established live causes or approved claims of a speedup.

The CodeServer workspace is Preview, separate from Production. Its process,
memory, or database timings must not be described as live Production metrics.
No internal diagnostic credential will be introduced or security policy weakened.

## Verification, preservation, and next action

Completed: fresh production ref and PR #2058 metadata comparison; exact prior
evidence branch fetch and SHA verification; applicable AGENTS.md, task continuity,
and Issue #1006 inspection; read-only independent frontend/backend path reviews;
production administration and live-page observations described above.
No new implementation test has completed at this checkpoint. No new PR or CI
result exists. This is a **WIP diagnosis checkpoint**, not release-ready code.

Next: obtain bounded current runtime evidence through supported administration or
the authorized terminal; resolve the service pressure demonstrated in the logs;
finish the scoped loader recovery and its focused regression tests; independently
review any patch and follow protocol v5 with a fresh source/intent pair, actual-host
rehearsal, owner lease, exactly one authorized publication, and three original
guard probes. Live acceptance must include a cold initial home navigation.

Preserve unrelated shared /app files and leases. Check the original guard's
fresh inactive status before any /app mutation or rehearsal. Do not reset hard,
clear another owner's lease, call paid chat, or test with real financial writes.
Durable task documents and PR metadata carry this handoff; no Issue #1006 comment
or other outbound message was sent.

## Verified catalog checkpoint (still WIP)

Predecessor diagnosis checkpoint: `584e6baea30d011f114648c6b266221a2521d9bc`.
Two backend files now have a focused, independently reviewed repair:

- `backend/operational_balance_sources.py`: the courier directory reads only
  owner identity and complete courier subdocuments, excluding unrelated setup
  history. Other financial/contract readers of that collection stay unchanged.
  Advertising account matching indexes ordinary exact-string identities while
  preserving duplicates, case, provider checks and legacy non-string comparisons.
- `backend/tests/test_operational_catalog_read_scope.py`: 21 in-memory cases
  cover output equivalence, real query projection size, ambiguous setup rejection,
  owner filtering, identity/error semantics and matching operation counts.

Red baseline: four failures and 17 passes. The failures expose excess shipping
payload in both directory modes and quadratic ordinary account matching. Green:
21 focused cases, followed by 153 passing cases in 19.79 seconds across the new
file, existing operational source, ad funding, workspace, and context-reader
modules. The test interpreter is an existing isolated scratch environment;
`PYTHONDONTWRITEBYTECODE=1`, `PYTHON_DOTENV_DISABLED=1`, and
`PYTHONPATH=backend:backend/tests` prevent live configuration use. The first
backend-only PYTHONPATH attempt failed collection on a sibling test import;
correcting the fixture import path resolved it without code/dependency changes.
Whitespace validation passed. Independent read-only review found no material
semantic mismatch. Tests use synthetic data only and no live writes.

Measured synthetic matching work drops from N-squared to 2N identity reads for
ordinary strings: 16,384 to 256 at 128 rows, and 262,144 to 1,024 at 512 rows.
Malformed/non-string-heavy catalogs deliberately retain the old fallback;
there is no universal linear-complexity claim. A separate synthetic shipping
example reads 194 bytes instead of 1,330,137 with identical directory output.
These are not measurements of production payload, latency, or memory.

Fresh hosting-terminal probes at 11:33:31 UTC returned HTTP 520 for both public
`/api/live` and `/api/health`, in 0.34 and 0.27 seconds respectively. No runtime
identity could be obtained from those failures. Later Production logs around
11:39:45–11:40:25 UTC show assembly reads at 160.9 seconds, DB-free health at
4.9–5.9 seconds, and multiple other routes at 13–25 seconds, plus recurring
upstream health timeouts. Broader pressure persists and is not declared fixed.

The original shared `/app` guard was read freshly: inactive, tracked source
clean at the production base, 417 untracked files preserved. The restored
Preview supervisor uses a separate retained supplier service; it was not changed.
Its configured database returned no current operational-state samples and its
existing diagnostics token is absent. No credential was created or extracted,
no protected diagnostic request was sent, and no Preview measurement is being
treated as Production evidence.

Frontend recovery tests and an investigation of cooperative scheduling inside
the measured slow canonical order-batch path remain in progress. Do not freeze
release intent or publish this checkpoint. Next: complete scoped red/green
verification, preserve canonical order/permission behavior, review independently,
then follow the existing fresh protocol-v5 release sequence and live acceptance.

## Reviewed source candidate

Predecessor catalog checkpoint: `35512ed7b09a5c6ef6e5a550a018559e3e5fe4af`.
All intended runtime edits are complete and independently reviewed:

1. The courier/ad catalog read reductions documented above.
2. The existing CPU budget now yields between complete canonical order
   hydration/mapping units in `order_engine/repository.py` and `service.py`.
   Raw projections, mapping, invalid-row handling, duplicates, page totals,
   grouping, pagination and monetary data remain unchanged. Cancellation at
   the new repository checkpoint joins one cursor-close operation even through
   repeated cancellation. The existing active-driver cancellation path is not
   changed. This is cooperative scheduling, not a hard single-order deadline.
3. Only the initial finance context opts into a 30-second complete visible
   attempt. Hidden/offline entry, interrupted attempts, timeout and missing
   authentication ID show explicit recovery instead of an indefinite loader.
   A still-pending physical request retains its lane; stale identities cannot
   publish a result. Other balance reads retain their original lifecycle.

Verification at this complete-source boundary:

- Frontend baseline: nine failures and two passes; after correction, all 11 new
  cases pass. Five focused suites pass all 94 cases on pinned Node 22.23.2 and
  Yarn 1.22.22. Exact commands and report hashes are in
  `evidence/frontend-verification-summary.json`.
- Backend catalog affected suites: 153 passed. Order-batch focused baseline:
  seven failures; after correction, seven passes. The affected order-engine,
  mapper, fulfillment and assembly batch has 122 passes and 41 explicit
  isolated-Mongo skips. Exact commands, warnings and limits are in
  `evidence/backend-verification.md`; skipped tests are not claimed as passing.
- Root integration on the combined current code: 72 passed, zero failures or
  skips, 16.04 seconds. Command: the same isolated Python/disabled-dotenv setup
  with `--noconftest -p no:cacheprovider -q --tb=short
  -o asyncio_default_fixture_loop_scope=function`, running
  `test_operational_catalog_read_scope.py`,
  `test_operational_balance_workspace.py`, and `test_order_batch_cpu_fairness.py`.
  The only warning is the existing fitz deprecation.
- Actual-diff independent reviews for frontend, catalog and order fairness found
  no material blocker. No real browser/transport or production-recovery result
  is inferred from these local checks.

The code-server `/app` volume has only about 92 MiB free. An initial default
toolchain preparation failed with no space; no source or existing outputs were
removed. A second preparation in an isolated `/tmp` cache on the available
filesystem succeeded using the original verified toolchain code, after a fresh
inactive guard check. The actual-host rehearsal will use an isolated checkout
and cache there, preserving shared services and the 417 unrelated untracked files.

No ASGI lifecycle wrapper, driver deadline, new cache, truncation or speculative
client polling change is included. The assembly response's 15-second polling
hint does not prove the absent mobile client's request behavior. Broad production
pressure and the cause of HTTP 520 remain unproven until live verification.

Next safe step: freeze this complete source as A, run two clean pinned frontend
builds with the reviewed public client origin, freeze intent-only B, require
fresh exact-B CI including isolated database coverage and host adapter checks,
then perform the original guard's actual-host release protocol. Publication is
already authorized, but this source checkpoint itself is not a deployed result.
