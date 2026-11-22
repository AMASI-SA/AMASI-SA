# Operational demand reads — implementation checkpoint

## Scope and authority

User requested implementation, testing, merge and publication of a comprehensive
Mezan operational performance improvement. On 22 November the user explicitly
approved evaluating and incorporating the supplied Codex performance handoff.
Keep exact money, currencies, commissions, evidence, refunds, permissions and
Qoyod behavior; preserve employee operations and existing cancellation behavior.
No Production pressure tests, external synchronization, background reconciliation,
or computation for unopened pages is authorized by opening a page.

Repository: `AMASI-SA/AMASI-SA`.
Task branch: `codex/operational-load-stage-diagnostics-20261122`.
Verified Production base: `c302943e330bcdd534a4a5697b8ef813dfb7cb6e`.
This is an explicitly incomplete implementation checkpoint, not a release.

## Fresh source and live evidence

The Production branch was fetched and matched the base above. Live health and
the browser-visible canonical `/build-meta.json` both identify source
`d396049f8fedf72c394169a451d45b227ad52376`, already deployed through the protocol-v5
intent-only/merge handoff in PR 2059. Frontend artifact is
`1aa92ab8ee890e1deb23f3c6742d75bb5748401b73ac45eb17e7d86aee378a88`.
Health reported verified identity, critical backend hashes and frontend build true.
Runtime ID: `rg5-aceb392af7809d9099ba92b27d4bb3d802999148c91013a5bba1cca5ad5f6c8e`.
Local HTTP access to build metadata returned 403; browser JSON succeeded. No
cause is inferred from the local status. This diagnostic work has not deployed.

Production Mongo viewer authentication completed after the user's specific
authorization through secure credential entry. Only collection metadata and UI
capabilities were inspected; no financial mutation, index change, executionStats
or explain was performed. The viewer did not expose a query-plan interface.
No credentials, customer documents or private URLs are retained here.

The Windows study files named by the user (IMPLEMENTATION-HANDOFF-ar.txt,
REPORT-ar.txt, saved-read-profile.json) are not available in this workspace.
Their contents were not read. Implementation uses the supplied message and
fresh repository evidence; historical reported test counts are not new results.

## Completed diagnostic foundation

- Fixed-label sampled read phases for context/home and assembly, including worker
  queue wall time, synchronous thread CPU, unchanged cursor advancement and
  bounded row counts. No user IDs, amounts, queries or exception text are logged.
- At most one active sample and one sample **start** per minute per scope/process.
  This is not a promise of one completion log per minute when reads run long.
- No financial formulas, queries, cursor ownership, cancellation, existing dispatch
  or DTO ordering were changed by this foundation.
- New diagnostic cases are explicitly collected and guarded against missing/skipped
  suites by operational CI.

Fresh integrated command (exit 0):

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 PYTHONPATH=backend:backend/tests \
  /workspace/scratch/81f2839b514c/courier-test-env/bin/python -B -m pytest \
  --noconftest -p no:cacheprovider -q --tb=short \
  -o asyncio_default_fixture_loop_scope=function \
  backend/tests/test_read_path_diagnostics.py \
  backend/tests/test_operational_context_read_diagnostics.py \
  backend/tests/test_assembly_read_diagnostics.py
```

Result: **40 passed**, 6.61 s, one existing fitz deprecation warning. An initial
invocation omitted PYTHONPATH and failed collection; corrected invocation above
is the integrated behavioral result. Agent affected suites additionally passed,
but are not substitutes for final integrated verification.

## Implementation plan in progress

1. Lightweight bootstrap independent of financial/catalog reads; current-page-only
   home, server-paged directories and intent-specific entry choices.
2. Durable demand-only read artifacts, separately scoped by fresh actor, effective
   permissions, client/page, exact snapshot revision and query filters. Serve
   bounded indexed pages; keep opening/closing balances from the complete period.
3. One bounded CPU child process through the supported worker, with demand expiry,
   duplicate coalescing, claim/generation fencing and crash recovery. Do not revive
   the legacy operational reconciliation worker or interfere with Qoyod/AI jobs.
4. Integrate existing canonical dashboard card and assembly calculations. For
   sources without a complete revision, expose dated display freshness honestly;
   never treat a cache as payment authority.
5. Controlled same-data cold/warm/concurrent before/after correctness and work/latency
   measures, final CI, protocol-v5 A/B build/rehearsal, lease check and authorized
   publication. Verify live pages afterward without load testing Production.

Acceptance targets (unproven): zero closed-page loading; no full rebuild for each
stable filtered ledger page; >=80% measured redundant-work reduction; ready-read
p95 <1 s and useful home data <2 s in a controlled environment. No platform-wide
improvement percentage is claimed.

## State and next safe action

Production changed by this task checkpoint: **no**. Prior PR 2059 publication is
already complete; do not repeat it. No new lease, merge or publish occurred.
All other conversations' work and leases must be preserved.
Next: implement and verify the demand-read vertical slice in this task worktree;
checkpoint subsequent verified milestones. Re-read current Production and guard
state immediately before a release, not from this historical note.
