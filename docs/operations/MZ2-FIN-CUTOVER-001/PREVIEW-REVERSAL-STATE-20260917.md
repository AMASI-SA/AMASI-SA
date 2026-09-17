# P01 settlement reversal state

Scope: isolated Preview only. Not deployed.

Root cause: generic ledger reversal changes the ledger entries but settlement API reads the immutable posting snapshot's status. Both register and draft API therefore displayed posted after full audited reversal.

Fix: shared read projection derives reversed only for a linked, owner-scoped group with at least two settlement legs, all reversed with reversal references and metadata.txn_type=provider_settlement_v2. Partial/missing/foreign evidence never claims full reversal. Preserve original document and expose stored_status/status_source. Apply effective status before status filtering; draft mutation guards reject reversed as terminal. Add Arabic reversed badge to draft UI. No migration or manual status rewrite.

Verification: 24 tests passed via /root/.venv/bin/python -B -m pytest equivalent pytest.main invocation preloading candidate modules (required because /app test configuration alters sys.path). Suites: new test_mz2_settlement_ledger_state.py; existing test_mz2_settlement_register_p01.py; test_mz2_settlements_p01.py. Candidate /tmp/mezan-settlement-reversal-20260917 imports precede /app/backend. One harmless anyio assertion-rewrite warning. Fresh read-only candidate _draft_or_404 on actual Preview synthetic fixture returns reversed; underlying record byte-for-byte equal as document, real statement6743152 unchanged. No financial writes. Syntax and git diff --check pass.

Initial tests exposed test dependency setup/path problems; actual DB check then exposed txn_type nesting under metadata. Both corrected and fresh tests plus actual read passed. Do not cite initial runs as passes.

Deployment blocked: Release Guard active=true on both checks. Runtime not changed or restarted. Production unchanged. Frontend build/UI verification pending. Existing register query's 2000-document cap is unchanged; filtered draft queries currently gather candidates before final effective-state limit.

Next: once lease owner closes active lease, compare exact runtime source hashes against task source; install in Preview overlay with rollback and integrity manifest; verify effective status in register, detail, draft, filters; ensure no financial records changed. Keep Production release gates closed. Do not rerun posting fixture or reverse entries again.
