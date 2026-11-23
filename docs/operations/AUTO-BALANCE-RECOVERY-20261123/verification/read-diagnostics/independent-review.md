# Independent diagnostics review — APPROVED

Reviewer: `/root/historical_recalculation_contract`.

Baseline: `422fffc8fb9c381165cc76b1ded5ca595773ce8e`.
Patch SHA-256: `887b2cdf1ec505876fe6d6d8db1d7ec5e1cdad0758be2381774d024c9fafbebf`.
Exactly seven changed paths and `git apply --check` against the immutable baseline passed.

## Reviewed hashes

| File | SHA-256 |
| --- | --- |
| `backend/operational_demand_refresh.py` | `e3eae95b059209d8365044d5e5bed05f25a78e668671eb0d3e2c25ac3e11563d` |
| `backend/operational_read_runtime.py` | `813b62bb2afabd447a527b7c262fcf15a280a0c86746d9f4e8cb0e5987fe7c90` |
| `backend/operational_balance_sources.py` | `b9e10ef40ade5fd67369b266b75fa24ebe9696ac3951632aec1d2c19246e637a` |
| `backend/operational_balance_store.py` | `0c702206368bb919c0e57fa93841023126753cdbf861c65d361e379062311a83` |
| `frontend/src/pages/operational/visibleOperationalRead.js` | `865b93aafed038e48d6456469b56caf4ab77fa287ce092ca45b576f10cbbd221` |
| `backend/tests/test_operational_refresh_read_diagnostics.py` | `10785edec5c756ee964c48081cdf5941c61c94a6a9e98c4b4552c72c53dd4fd0` |
| `frontend/src/pages/operational/visibleOperationalRead.test.js` | `801dfff16079ea8820481cbcb92f17151d1cde9033680e6c828fb4852a553b28` |

## Independently executed checks

From `/workspace/scratch/bc2035025680/recovery-read-diagnostics-20261123`:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend \
../backend-freshness-venv/bin/python -B -m pytest \
  --noconftest -p no:cacheprovider -q \
  backend/tests/test_operational_refresh_read_diagnostics.py \
  backend/tests/test_operational_balance_demand_refresh.py \
  backend/tests/test_operational_read_runtime.py \
  backend/tests/test_operational_balance_source_diagnostics.py
```

Result: **238 passed in 3.33s; exit 0**. All seven hashes remained unchanged.

Independent frontend reviewer `/root/historical_recalculation_contract/source_date_rules` ran:

```bash
node ../web-freshness-deps/node_modules/jest/bin/jest.js \
  --config local-results/jest.config.cjs --runInBand --no-cache
```

Result: **73 passed; one suite passed; exit 0; Jest 1.776s**. Both frontend candidate hashes and immutable D baseline blobs were verified and remained unchanged.

## Review findings

No blocking finding. Specification and engineering verdicts: PASS.

- Admission, lease, CAS and unchanged source helpers are AST-identical to D. The store matches D after removal of timing wrappers.
- The 15s/85s deadlines, 12 retries, full source evidence and 20,000-row cap plus overflow sentinel remain intact.
- No added asyncio tasks or scheduling sleeps. Controlled Motor tests preserve owned reads, joining, sequential close and no new batch after cancellation.
- Observations are request-local and limited to 12 reconstructed records. Identifiers, queries, raw rows, tokens and exception text are excluded.
- Canonical 408/409 behavior and unrelated errors remain unchanged. Diagnostic failures cannot replace operational outcomes.

## Limits and next gate

Timings overlap: do not sum `read_elapsed_ms`, `cancel_join_ms` and `close_ms` or infer exclusive database/CPU cost. No live or financial actions were performed, and this review does not establish historical-balance recovery.

Combined health-authority/diagnostics review is pending the final source SHA from root. Current-HEAD CI and A/B, merge and deployment gates remain separate. Broad tests will not be repeated without a concrete interaction risk.
