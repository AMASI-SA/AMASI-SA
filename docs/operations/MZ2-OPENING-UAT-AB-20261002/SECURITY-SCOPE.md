# Security Gate source-scope correction

Classification: **B — CI integration regression**, resolved through source-scoped test wiring. No new permission, economic contract or runtime change.

Starting PR #1247 HEAD `3b6683a5dca9fb520b934abb65df2d8581b44d8f`, tree `d47cfb8c71c3ee41de25409d349bdc0fc9d46ee5`. Its common ancestor with Production remains `78dcf31af73581ceba3677c464c657a0b9b2c4fc`. Production advanced externally through BUILD37 PR #1246 to `8a9c35fd27e256d690831fc19ce649e079cc04ca`; this task does not merge, rebase or retarget the PR.

## Cause and reproduction

The `pull_request` event resolves its workflow on the synthetic merge ref, while `.github/workflows/security-gate.yml` explicitly checks out `github.event.pull_request.head.sha`. The current base added `backend/tests/test_build37_preparation_file_permission.py` to the inline command. That file is absent from the independent Opening source, and its newer mobile preparation permission binding is absent too. This mismatch produced pytest exit 4 in [exact-head Security run 37074662214](https://github.com/AMASI-SA/AMASI-SA/actions/runs/37074662214/job/111061754064), before the security-contract step ran. A local `--noconftest --collect-only` invocation reproduced the missing-file failure without loading runtime modules.

GitHub documents [pull-request merge refs and head checkout](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#pull_request). The workflow event and checkout source are different boundaries; the old `base_sha` metadata does not freeze the workflow against later base-branch changes.

## Minimal fix and invariants

- The workflow preserves exact-head checkout and all existing audit, compile, core, inherited-security and frontend security checks. Only the core pytest invocation delegates to `scripts/run_security_contracts.py`, with the same test arguments. It also runs the new scope regression suite and passes the expected source SHA.
- The runner keeps all 11 existing core security files mandatory, even if a caller omits a core argument. Other requested tests also remain mandatory; unknown missing files fail closed.
- Scope is computed from the checked-out files only, never branch names, changed-path labels, remote Production, or the workflow's merge SHA. The runtime module is parsed as AST, not imported: its import-time operational guards are never executed by scope detection.
- BUILD37 is non-applicable only when its test is absent **and** the source contains the explicit old preparation binding (`app.page.my_products` alone). A changed literal binding makes the test required. Missing, malformed, ambiguous or unknown source shape fails closed.
- If the BUILD37 test exists, it runs regardless of the binding or caller arguments. If its contract is present but the test is missing, the gate fails instead of reporting a skip. This prevents deleting an applicable test from turning CI green.
- The runner checks the actual checkout SHA against the expected PR/source SHA, logs that identity and selected paths, and propagates pytest's exit status unchanged. It never catches assertion failures, retries tests, disables a query, or applies suppression.

No BUILD37 runtime/test file is copied or added to this branch. The only new backend file is a standard-library CI-scope test using synthetic temporary fixtures, not the BUILD37 permission implementation/test.

## Evidence

Focused command (repository root):

```text
python -B -m unittest -v backend.tests.test_security_gate_source_scope
```

**11 PASS**, covering the old MZ2 binding plus foreign workflow argument; contract-present/test-missing failure; present BUILD37 test inclusion; every core file remaining mandatory; other requested files; unknown source shapes; missing/malformed source; changed source SHA; and preservation of pytest exit codes 0/1/2/4/5. The workflow contract test preserves the original core list and inherited-security invocation.

Fresh Security, CodeQL, G47, Accounting, backend integration, full frontend, real Mongo, browser fixtures and build results must be read on the exact pushed HEAD. The final immutable job matrix, artifact IDs and HEAD/TREE are recorded in Issue #1006 and PR #1247, not inferred from the previous 27 successful jobs.

Production financial writes by this task = **0**. No financial runtime, writers, ledger/journal, C3, Track F, 409/423, write-control, Opening, Inventory Initialization, Activation/P08 or G47 sequencing change. No merge/deploy/release preparation or real balance entry. Existing C gates and Full Business UAT hold remain. Live Browser UAT is still blocked by kernel assets; CI browser fixtures are isolated evidence only.
