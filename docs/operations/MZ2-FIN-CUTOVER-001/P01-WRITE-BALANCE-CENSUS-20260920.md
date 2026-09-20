# P01 write-balance consumer census — 2026-09-20

Status: static source audit, not runtime acceptance. P01 remains IN_PROGRESS; P02 remains LOCKED. No Production changes.

## Scope and reproducibility

Frozen accepted report source: `163874f5ffcab10a5f3dc883a1c2acd56c414bf9` (#1110 A). Its A/B and evidence are preserved. This index captures the WIP source **after** replacing the three P01 consumers with `accounting_mz2_balances.read_mz2_write_balances`; it is not a new frozen release identity. The CSV includes a SHA-256 per indexed file. Re-run the census after integration onto the freshly fetched Production J; do not treat this checkpoint index as evidence of an uninspected future tree.

Search roots: `backend`, `frontend/src`, `scripts`; source extensions `.py`, `.js`, `.jsx`, `.cjs`, `.mjs`, `.ts`, `.tsx`. The literal expression is `compute_balance|compute_balances_bulk|account_balance_ssot|current_balance`. Comments/imports, exact symbols, substring-only hits (for example `_recompute_balance`), and test fixtures are retained rather than silently discarded. Documentation/generated outputs are not executable source. Python function attribution comes from the smallest enclosing AST function; JS/JSX attribution is module-level and exact line numbers remain available.

Full occurrence index: [CSV](P01-WRITE-BALANCE-CENSUS-20260920.csv). One record per matching source line; multiple matching symbols on one line are listed together. Test records are explicitly separated from production decisions.

## P01 write decisions: six original call sites

| File / enclosing function | Accepted A location | Purpose | Current replacement / verdict |
|---|---:|---|---|
| `accounting_settlement_service._post_reviewed_settlement_transaction` | 505 | Cap provider receivable closure | One transactional MZ2 snapshot; `net_balance(payment_gateway, provider, receivable)`; required now |
| `accounting_customer_refunds.post_bank_payment` / nested `write` | 188 | Bank/main or selected provider/receivable execution funds | Same MZ2 snapshot as case reconciliation; execution channel remains independent of original provider; required now |
| Same | 191 | Customer refund case payable equals remaining due | `net_balance(liability, case_id, customer_refund_payable)`; required now |
| `accounting_customer_advances.cancel_advance` / nested `write` | 142 | Original customer advance equals captured amount | `net_balance(liability, advance_id, customer_advance)`; required now |
| `accounting_customer_advances.pay_advance` / nested `write` | 210 | Bank/provider execution funds | Transactional MZ2 snapshot; required now |
| Same | 211 | Advance refund case payable equals remaining due | `net_balance(liability, advance_id, customer_refund_payable)`; required now |

The old imports at refund184, advance141/208, settlement18 are part of these call-site replacements, not extra decisions. The helper asserts `SessionDatabase`, owner identity and an active Mongo transaction, then calls the accepted `read_mz2_ledger` with that same scoped database. Non-available readiness produces HTTP409; no separate raw database read, legacy fallback, current_balance read, or copied report eligibility algorithm is introduced. This is static verification only; sentinel results belong in the coordinator's runtime evidence.

## MZ2 report reads already covered by #1110

`accounting_mz2_reports.read_mz2_ledger` is the shared eligibility primitive: fixed operation, server owner, approved opening group/zero evidence, validated producers/groups and accounting dates. Financial position, trial balance, journals, home balances and settlement journal detail consume that boundary. These files contain no calls to the four searched legacy symbols; this absence is why they do not appear as false-positive legacy consumers in the CSV. Keep their accepted behavior unchanged.

`accounting_receivable_service.prepare` and advance/refund identity checks also inspect old journals to **reject duplicate recognition/execution**. Those are conflict/evidence checks, not spendable-balance readers; do not remove those guards as part of balance isolation.

## Explicit P02 exception and gate risk

`store_delivery_accounting.store_driver_ledger_balances` calls legacy `compute_balance` at283/290 for `store_driver/cod_receivable` and `store_driver/delivery_fee_payable`. This is genuinely Mezan2 code: its OPERATION_ID at26 matches this task. It is classified **MZ2 shipping/P02 outside current P01**, not mislabeled legacy. This phase boundary is explicit in `README.md:84` and `STATUS.json:180-186` (P02 shipping/COD/store drivers, blocked by P01, exit gate LOCKED). `post_settlement_journal` uses these balances as posting caps at338; `_totals` in `store_delivery_settlement_routes` uses them for read summaries at106.

Static reachability: `order_engine/__init__.py:209` registers the settlement router; routes `/store-delivery/settlements/driver/{driver_id}/...` call `_post`, which calls `post_settlement_journal` at199. The inspected settlement path checks accountant permission, owner, account and amounts but does not enforce the P02 phase lock, full approved-opening gate or `atomic_owner` barrier. The existence of the documentation lock alone therefore does not prove endpoint inaccessibility. This is a separately recorded P02 activation risk; no shipping behavior is changed by the P01 task. The accepted P01 report producer allowlist intentionally excludes `store_delivery_*`; it must not be expanded casually to mask this difference.

## Production baseline delta inspected read-only

Local objects permit comparison `ebf92e6606a810a1e3f0a0e2026984a5482f71bc..80d84ffc599dc373dbb4a2bb45e226fa07ffde4a`: 13 files changed, chiefly customer-cohort profit report/backend route, frontend report/service/navigation/tests, `.gitignore`, its task status, and release intent. None of the three P01 writer files, shared MZ2 report eligibility or new balance helper is changed by that delta. This does not establish that `80d84` is still current remote HEAD; coordinator must fetch and record actual J. Preserve customer-cohort work and do not transfer old B's release-intent as a source change.

## Complete production-source grouped index

All rows below other than the explicit P02 row are legacy/unrelated and remain unchanged. Function/line lists include comments and imports; the CSV distinguishes substring-only matches. Tests are fully indexed in CSV but omitted from this production table.

| File | Function : lines | Classification / purpose |
|---|---|---|
| `backend/account_balance_diagnostic_iter246i.py` | `<module/JS source>`: 5,6,9; `account_balance_audit`: 36,42,44,46,48,53,55,57,62,64,105,109 | legacy_unrelated — Existing general-ledger/account diagnostic or legacy reconciliation; preserve |
| `backend/account_tx_vs_ledger_walk_routes.py` | `<module/JS source>`: 26; `walk`: 339,359,408 | legacy_unrelated — Existing general-ledger/account diagnostic or legacy reconciliation; preserve |
| `backend/accounts_balance_diagnostic_routes.py` | `<module/JS source>`: 6; `accounts_balance_diagnostic`: 68,73,101,103,122,131,133,242; `accounts_balance_repair_preview`: 656,723,736; `bnpl_balance_source_comparison`: 857,887,986 | legacy_unrelated — Existing general-ledger/account diagnostic or legacy reconciliation; preserve |
| `backend/accounts_routes.py` | `<module/JS source>`: 18,19; `_recompute_balance`: 439,442,473; `_account_with_meta`: 487,491,493,496,497,500,501,502; `summary`: 539,542,551,555; `create_account`: 602,629; `get_routing_map`: 673; `account_breakdown`: 757; `create_transaction`: 938; `delete_transaction`: 954; `sync_payment_methods`: 1263,1264,1281,1282,1298,1346,1370,1399; `ensure_default_banks`: 1445 | legacy_unrelated — Existing shared/legacy account, employee, supplier, courier or BNPL workflow; outside P01 router |
| `backend/ad_account_actual_debt_routes.py` | `dryrun`: 100,116,123 | legacy_unrelated — Advertising balance/spend workflow; outside P01 |
| `backend/ad_account_dryrun_diff_routes.py` | `dryrun`: 73,90,117,129,151,220,275,299 | legacy_unrelated — Existing general-ledger/account diagnostic or legacy reconciliation; preserve |
| `backend/ad_account_forensic_routes.py` | `<module/JS source>`: 9,40; `catalog`: 253; `live`: 285,386,390,439 | legacy_unrelated — Existing general-ledger/account diagnostic or legacy reconciliation; preserve |
| `backend/ad_account_recompute_dryrun_routes.py` | `recompute_dryrun`: 56,77,88,97,107,138,245,249,253,293,294 | legacy_unrelated — Existing general-ledger/account diagnostic or legacy reconciliation; preserve |
| `backend/ad_account_root_cause_routes.py` | `root_cause`: 574,575 | legacy_unrelated — Existing general-ledger/account diagnostic or legacy reconciliation; preserve |
| `backend/ad_account_routes.py` | `_post_spend_to_ledger`: 445; `_post_bank_tx`: 559,560; `edit_topup`: 1252,1253,1271,1272; `migration_preview`: 1996; `diagnose_duplicate_topups`: 2722 | legacy_unrelated — Advertising balance/spend workflow; outside P01 |
| `backend/ad_spend_rca_routes.py` | `_ui_balance`: 354,355 | legacy_unrelated — Advertising balance/spend workflow; outside P01 |
| `backend/ad_spend_scheduler_diagnostics.py` | `scheduler_diagnostics`: 138 | legacy_unrelated — Existing general-ledger/account diagnostic or legacy reconciliation; preserve |
| `backend/ad_spend_windows.py` | `<module/JS source>`: 51; `_post_one_window`: 215 | legacy_unrelated — Advertising balance/spend workflow; outside P01 |
| `backend/audit_routes.py` | `<module/JS source>`: 21; `post_migration_audit`: 102,104,116,252,256,258; `forensic_report`: 736,742,743,838,839,845,850,856; `tabby_phase2`: 913,921,922,1159,1160,1162,1215,1216,1226,1315 | legacy_unrelated — Existing general-ledger/account diagnostic or legacy reconciliation; preserve |
| `backend/balance_drift_diagnostic_routes.py` | `<module/JS source>`: 5,9; `balance_drift`: 192,196,198,215,230,233,289,362 | legacy_unrelated — Existing general-ledger/account diagnostic or legacy reconciliation; preserve |
| `backend/balance_resolver.py` | `<module/JS source>`: 10,13,15; `resolve_live_balance`: 35,40,41,45,70,93,96; `resolve_live_balance_by_id`: 112 | legacy_unrelated — Existing shared/legacy account, employee, supplier, courier or BNPL workflow; outside P01 router |
| `backend/balances.py` | `compute_balances`: 44 | legacy_unrelated — Existing shared/legacy account, employee, supplier, courier or BNPL workflow; outside P01 router |
| `backend/bank_balance_subaccount_diagnostic_routes.py` | `<module/JS source>`: 5,8,10,11; `diag`: 49,62,172,173,174,175,181,182,185,189,212,219,226,247,250,283,296,301,337 | legacy_unrelated — Existing general-ledger/account diagnostic or legacy reconciliation; preserve |
| `backend/bank_current_balance_source_routes.py` | `<module/JS source>`: 1,4,8,9; `make_bank_current_balance_source_router`: 39; `diag`: 61,65,69,70,203,242,252,253,264,265,279,280,289,307,310,320,321,326,331,332,338,339,341,344,347,348,350,354,356,360,364,368,377,380,383,406,407,408,441,442,443,452,453,457,463 | legacy_unrelated — Existing shared/legacy account, employee, supplier, courier or BNPL workflow; outside P01 router |
| `backend/bnpl/settlement_bridge.py` | `_current_receivable`: 58,59; `post_bnpl_settlement_to_ledger`: 372 | legacy_unrelated — Existing shared/legacy account, employee, supplier, courier or BNPL workflow; outside P01 router |
| `backend/bnpl/settlements_routes.py` | `register_settlement`: 205,206; `backfill_bank_transactions`: 311,314; `registration_overview`: 726,742 | legacy_unrelated — Existing shared/legacy account, employee, supplier, courier or BNPL workflow; outside P01 router |
| `backend/bnpl_settlement_banktx_routes.py` | `apply`: 171 | legacy_unrelated — Existing shared/legacy account, employee, supplier, courier or BNPL workflow; outside P01 router |
| `backend/bnpl_settlement_health_routes.py` | `_provider_health`: 35,36 | legacy_unrelated — Existing shared/legacy account, employee, supplier, courier or BNPL workflow; outside P01 router |
| `backend/bnpl_settlement_trace_routes.py` | `trace`: 79,123 | legacy_unrelated — Existing general-ledger/account diagnostic or legacy reconciliation; preserve |
| `backend/bnpl_statement_ui_audit_routes.py` | `trace_account_statement`: 474,557 | legacy_unrelated — Existing general-ledger/account diagnostic or legacy reconciliation; preserve |
| `backend/cod_diagnostic_routes.py` | `cod_source_breakdown`: 74,125,185,261,266,307,310,322,347,352,361,373,380 | legacy_unrelated — Existing general-ledger/account diagnostic or legacy reconciliation; preserve |
| `backend/employee_orphan_diagnostic_routes.py` | `employee_orphan_openings`: 168,415,444,446,447,448,452,457,496; `archive_legacy_orphans`: 670 | legacy_unrelated — Existing general-ledger/account diagnostic or legacy reconciliation; preserve |
| `backend/expenses_routes.py` | `_recompute_account_balance_for_expense`: 232,233,257 | legacy_unrelated — Existing shared/legacy account, employee, supplier, courier or BNPL workflow; outside P01 router |
| `backend/financial_movements_routes.py` | `_resolve_account`: 145; `accounts_with_availability`: 450,453,454,460,469,471,475,478,482,494,495; `create_movement`: 675,705,890,893,895,964,965 | legacy_unrelated — Existing shared/legacy account, employee, supplier, courier or BNPL workflow; outside P01 router |
| `backend/financial_pages_inventory_data.py` | `<module/JS source>`: 18,54,339,409 | legacy_unrelated — Existing shared/legacy account, employee, supplier, courier or BNPL workflow; outside P01 router |
| `backend/financial_position_ssot.py` | `<module/JS source>`: 5,22,429; `account_balance_ssot`: 79,85,89,115,120,122,132,135,138,139,151,156; `compute_financial_position`: 367,373,378,383 | legacy_unrelated — Shared financial position and legacy account-balance fallback; MZ2 no longer consumes these in #1110 |
| `backend/ledger_core.py` | `compute_balance`: 496; `compute_balances_bulk`: 564,567 | legacy_unrelated — Shared legacy primitive definitions; do not change globally |
| `backend/ledger_routes.py` | `<module/JS source>`: 19; `get_balance`: 422 | legacy_unrelated — Existing shared/legacy account, employee, supplier, courier or BNPL workflow; outside P01 router |
| `backend/liabilities_routes.py` | `_recompute_account_balance`: 251; `employee_settlement`: 934,939,943; `summary`: 1356,1357,1363,1400,1408,1424 | legacy_unrelated — Existing shared/legacy account, employee, supplier, courier or BNPL workflow; outside P01 router |
| `backend/migration_routes.py` | `<module/JS source>`: 32; `_legacy_bank_balances`: 231,242,245; `_legacy_payment_platform_balances`: 273,274,328,329,337; `_new_ledger_balance`: 394; `_ledger_bal`: 804; `reconciliation_report`: 901,921,934,957 | legacy_unrelated — Existing shared/legacy account, employee, supplier, courier or BNPL workflow; outside P01 router |
| `backend/reconciliation_forensic_routes.py` | `reconciliation_forensic`: 86; `_ledger_balance`: 167 | legacy_unrelated — Existing general-ledger/account diagnostic or legacy reconciliation; preserve |
| `backend/reconciliation_routes.py` | `_platform_row`: 144,158,166,194; `reconciliation_summary`: 213 | legacy_unrelated — Existing general-ledger/account diagnostic or legacy reconciliation; preserve |
| `backend/reversal_impact_audit_routes.py` | `<module/JS source>`: 3,9,11; `reversal_impact_report`: 68,78,111,194 | legacy_unrelated — Existing general-ledger/account diagnostic or legacy reconciliation; preserve |
| `backend/salla_balance_forensic_routes.py` | `<module/JS source>`: 5,10,13; `salla_forensic`: 95,107,487,490,639,706,722,728,730,734,741,747 | legacy_unrelated — Existing general-ledger/account diagnostic or legacy reconciliation; preserve |
| `backend/server.py` | `<module/JS source>`: 162,4436,4437,4438,4441; `balances_endpoint`: 1778; `dashboard`: 2384 | legacy_unrelated — Existing shared/legacy account, employee, supplier, courier or BNPL workflow; outside P01 router |
| `backend/settlement_file_forensic_routes.py` | `<module/JS source>`: 12; `settlement_file_forensic`: 279,292,310,419 | legacy_unrelated — Existing general-ledger/account diagnostic or legacy reconciliation; preserve |
| `backend/settlements_import/service.py` | `<module/JS source>`: 6 | legacy_unrelated — Comment forbids modifying current_balance; not a balance read |
| `backend/shipping_accounts.py` | `_recompute_shipping_account_balance`: 54,77; `add_courier_transfer`: 782,791 | legacy_unrelated — Existing shared/legacy account, employee, supplier, courier or BNPL workflow; outside P01 router |
| `backend/store_delivery_accounting.py` | `<module/JS source>`: 17; `store_driver_ledger_balances`: 283,290 | MZ2_shipping_P02_outside_P01 — Driver COD/fee balances used by separate shipping reads and settlements; P02 remains locked |
| `backend/supplier_ledger_detail_routes.py` | `<module/JS source>`: 42; `supplier_ledger_detail`: 106,107 | legacy_unrelated — Existing shared/legacy account, employee, supplier, courier or BNPL workflow; outside P01 router |
| `backend/suppliers_report_routes.py` | `suppliers_report`: 101,103 | legacy_unrelated — Existing shared/legacy account, employee, supplier, courier or BNPL workflow; outside P01 router |
| `backend/suppliers_unification_forensic_routes.py` | `suppliers_unified`: 413 | legacy_unrelated — Existing general-ledger/account diagnostic or legacy reconciliation; preserve |
| `backend/tamara_apply_routes.py` | `tamara_apply_execute`: 606 | legacy_unrelated — Existing shared/legacy account, employee, supplier, courier or BNPL workflow; outside P01 router |
| `backend/tamara_receivable_diagnostic_routes.py` | `tamara_receivable_breakdown`: 42,43 | legacy_unrelated — Existing general-ledger/account diagnostic or legacy reconciliation; preserve |
| `backend/transfers_routes.py` | `<module/JS source>`: 4,5,9; `attach_transfers_routes`: 64; `create_transfer`: 172,178,217,219,222,367,368; `delete_transfer`: 408,409 | legacy_unrelated — Existing shared/legacy account, employee, supplier, courier or BNPL workflow; outside P01 router |
| `backend/universal_accounting_routes.py` | `<module/JS source>`: 31,32,440,441,446; `_account_live_balance`: 453; `_ensure_opening_balance_seeded`: 473,485,489; `grant_advance`: 663,707; `grant_custody`: 757; `return_custody`: 772,815; `settle_custody_with_receipts`: 843,878; `transfer_custody_between_employees`: 910,955,958; `cash_accounts_with_balances`: 1386,1395,1396,1397,1401,1404,1407,1410,1419,1425; `employee_summary_balance`: 1452,1455,1458; `post_salary_accrual`: 1544; `settle_employee`: 1595,1600,1685,1688; `supplier_invoice`: 1726; `supplier_pay`: 1780,1866,1946,1949; `external_grant`: 2003; `external_collect`: 2052; `record_expense`: 2275; `courier_charge`: 2363; `courier_pay`: 2404; `courier_cod_deposit`: 2453; `courier_cod_settle`: 2499,2624; `entity_statement`: 2653; `employee_financial_summary`: 2671,2674,2677; `employees_with_balances`: 2734; `suppliers_with_balances`: 2852; `externals_with_balances`: 2957; `couriers_with_balances`: 2986,2989 | legacy_unrelated — Existing shared/legacy account, employee, supplier, courier or BNPL workflow; outside P01 router |
| `frontend/src/pages/AccountDetails.jsx` | `<module/JS source>`: 223,224,247,248 | legacy_unrelated — Existing legacy/shared page balance presentation or transaction input; not MZ2 isolated report |
| `frontend/src/pages/Accounts.jsx` | `<module/JS source>`: 172,648,651 | legacy_unrelated — Existing legacy/shared page balance presentation or transaction input; not MZ2 isolated report |
| `frontend/src/pages/AdAccountForensic.jsx` | `<module/JS source>`: 639,642,1061,1124,1130,1137,1142,1299 | legacy_unrelated — Existing general-ledger/account diagnostic or legacy reconciliation; preserve |
| `frontend/src/pages/AdAccounts.jsx` | `<module/JS source>`: 298,760 | legacy_unrelated — Existing legacy/shared page balance presentation or transaction input; not MZ2 isolated report |
| `frontend/src/pages/Advances.jsx` | `<module/JS source>`: 98 | legacy_unrelated — Existing legacy/shared page balance presentation or transaction input; not MZ2 isolated report |
| `frontend/src/pages/BalanceDriftDiagnostic.jsx` | `<module/JS source>`: 7,10,315,350 | legacy_unrelated — Existing general-ledger/account diagnostic or legacy reconciliation; preserve |
| `frontend/src/pages/CODDiagnostic.jsx` | `<module/JS source>`: 12,206,430,431,486,487,532,584 | legacy_unrelated — Existing general-ledger/account diagnostic or legacy reconciliation; preserve |
| `frontend/src/pages/FinancialInputHub.jsx` | `<module/JS source>`: 923,982,1292,1487,1560 | legacy_unrelated — Existing legacy/shared page balance presentation or transaction input; not MZ2 isolated report |
| `frontend/src/pages/OperatingExpenses.jsx` | `<module/JS source>`: 885,886 | legacy_unrelated — Existing legacy/shared page balance presentation or transaction input; not MZ2 isolated report |
| `frontend/src/pages/Receivables.jsx` | `<module/JS source>`: 178 | legacy_unrelated — Existing legacy/shared page balance presentation or transaction input; not MZ2 isolated report |
| `frontend/src/pages/Reconciliation.jsx` | `<module/JS source>`: 168,192 | legacy_unrelated — Existing general-ledger/account diagnostic or legacy reconciliation; preserve |
| `frontend/src/pages/ReconciliationReport.jsx` | `<module/JS source>`: 437 | legacy_unrelated — Existing general-ledger/account diagnostic or legacy reconciliation; preserve |
| `frontend/src/pages/ShippingAccounts.jsx` | `<module/JS source>`: 302,303 | legacy_unrelated — Existing legacy/shared page balance presentation or transaction input; not MZ2 isolated report |
| `frontend/src/pages/ShippingTransfers.jsx` | `<module/JS source>`: 48,149 | legacy_unrelated — Existing legacy/shared page balance presentation or transaction input; not MZ2 isolated report |
| `frontend/src/pages/Transfers.jsx` | `<module/JS source>`: 39,63,132,138,161,168,363 | legacy_unrelated — Existing legacy/shared page balance presentation or transaction input; not MZ2 isolated report |

Counts for this WIP snapshot: MZ2_shipping_P02_outside_P01=3 matched lines, legacy_unrelated=524 matched lines, test_only=244 matched lines; production-source files=66; total CSV records=771.
