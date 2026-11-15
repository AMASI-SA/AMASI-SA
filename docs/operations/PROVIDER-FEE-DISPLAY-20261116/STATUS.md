# Provider fee display correction — WIP

## Scope and current evidence

- Task: `PROVIDER-FEE-DISPLAY-20261116`; repository `AMASI-SA/AMASI-SA`.
- Source branch: `codex/provider-fee-display-recovery-20261116`.
- Parent and currently observed production source: `236a428364bc8163a684795ed1be87e237476f18`; tree `6967da090ac1301ca38bc8bc179758d7eaa91976`.
- Prior PR 1950 was successfully published and officially verified. This is a newly reported presentation defect, not a claim that the previous publication failed.
- User acceptance: monetary cells and brief statuses stay compact; explanatory prose, source evidence, assumptions and dates are visible below the provider table under `توضيحات`. Existing numeric review controls remain usable. No additional details dialog is introduced.

## Changes in this checkpoint

- Backend summary preserves the complete estimated charges already included in the known fee subtotal and provides explicit computational coverage, count and reason metadata. Existing nullable totals, fee deductions, principal, refunds, net recognition and pending-review identities retain their behavior.
- Financial-position presentation maps the preserved estimated subtotal, including genuine zero, without subtracting actual fees from a combined total. Mixed available/unavailable rows retain a known subtotal with explicit incompleteness.
- Frontend provides concise provider cells, moves explanations below the table on screen and print, and preserves complete export inputs and the existing review popup through its numeric trigger.
- Regression cases cover partial estimates, unknown versus zero, evidence replacement, held and unidentified claims, scope and exact decimal presentation, printing and export behavior.
- Changed code paths: `backend/operational_provider_summary.py`, `backend/operational_financial_position_core.py`, their focused tests; `frontend/src/pages/operational/OperationalFinancialPosition.jsx`, `PendingOrdersDialog.jsx`, new `ProviderFeeDetails.jsx`, scoped CSS and focused tests.

## Verification and coordination

- Fresh original environment observation: exact parent above, tracked/index clean, 120 unrelated untracked files retained, release guard inactive; no coordinator marker present.
- Isolated original-environment checkout: `/tmp/provider-fee-display-20261116`.
- Baseline regression executed there using the real module graph and `/root/.venv/bin/python`, exit 1: a supplied calculated estimate is hidden by the existing table projection. The synthetic fixture contains no merchant data.
- Durable baseline receipt and independently reviewed generic read-only diagnostic helper: branch `codex/provider-fee-display-evidence-20261116`, commit `fdce94ebd7dc07023b8e93ad20a0c9cc2d790187`.
- Backend syntax parsing and independent static review completed. Combined backend/frontend tests have **not yet run** for this checkpoint; no test, merge or release readiness is claimed.
- Read-only diagnostic helper transferred with verified SHA256 `c310ac2ce9fc218a5b3c30387a3292ad2e94f19fdf8c70ae43ca0fc1a7bd812d`. A narrowly scoped statement-owner lookup did not establish a unique match; no saved-state projection was run, no owner identifier was printed, and no financial data was written. The actual live incomplete-row predicate remains unproven.
- Issue 1006 has reached its existing comment limit; this task document and its draft PR preserve continuation evidence.

## Next safe actions

1. Verify this remote checkpoint SHA and fetch it into the isolated checkout; preserve the shared `/app` source and unrelated files.
2. Run the focused backend and frontend suites, inspect failures, and finish independent integrated review. Resolve the scoped read-only diagnostic target from authoritative schema/configuration before running it; do not broaden to other owners.
3. Record verified outcomes in another source checkpoint. Complete required CI, then freeze source A and an intent-only B using protocol v5.
4. Merge normally, confirm the resulting tree, perform the original-environment rehearsal while the guard is inactive, then use the session's existing publication authorization and verify the new production identity and live table.

Production changed in this task: **no**. No new lease, publication, source refresh, statement reimport, payment/refund confirmation or other financial write has occurred.
