# Track G read adapter contract

Verified source: `origin/review-h2-g`, commit `41cfa2b55ee3398190cc4e37395ba297f9ce49e4`.
The H2 worktree does not copy its backend. These are candidate contracts, not evidence that Production has deployed them.

`backend/accounting_onboarding.py` installs `accounting_onboarding_setup_routes.py` at `/accounting-module/onboarding`; the shared Axios client already supplies `/api`.

| GET path under that base | Contract used | UI meaning |
| --- | --- | --- |
| `/definitions` | `ssot_setup_version === 1`, `external_person_registry === mz2_external_persons_v2` | Mandatory gate before all other reads. Older Production contracts are blocked. |
| `/typed-facts` | `{items}` from `mz2_opening_facts_v2`; amount, currency, category, evidence, cutover_date | Documented opening facts, not live account balances. Sales VAT and input VAT remain separate rows. |
| `/fee-policies` | `{items}` from `mz2_provider_fee_policies_v2`; percentage/fixed/min/max/VAT treatment/effective dates | Saved provider policy, not fees computed or charged by frontend. |
| `/prepaid-candidates?cutover=YYYY-MM-DD` | `{items,blockers,source:operating_recurring_obligations_v2}` | Native recurring invoice candidates and backend calculation. Date is explicit; no browser-day cutover assumption. Currency/evidence may be missing. |
| `/identities/external_person` | `{items}` from `mz2_external_persons_v2`, id/label/kind | Identity only; no invented receivable/payable balance. |

Source calculations and source-stale/blocker markers are displayed as returned. Missing currency suppresses money denomination; there is no SAR fallback for returned facts. Missing amount is not zero. No aggregation, FX conversion, tax netting, payment, funding, posting or contract write occurs.

`ObligationsPanel` provides Arabic RTL tabs, search within loaded records, responsive internal table scrolling, loading/error/retry/empty/not-ready states and stale response suppression. The integration parent places this default component in the daily workspace.

Dependencies intentionally blocked: generic deposits; recurring runtime payments/amortization; current liability/person balances; tax periods and operational tax filing. Track G setup does not establish these runtime contracts. Do not use legacy liabilities, counterparties, accounts, settings, general_ledger, or old operating reports as alternative sources.

Validation: Node22 CRA Jest `--testMatch '**/ObligationsPanel.test.jsx'`:8 passed. Covers native capability gate, exact paths/query, actual zero versus missing, separate VAT, no mutations, loading/empty/error/404, and late-tab response isolation. Synthetic fixture demonstrates candidate responses only; not a live API integration test.
