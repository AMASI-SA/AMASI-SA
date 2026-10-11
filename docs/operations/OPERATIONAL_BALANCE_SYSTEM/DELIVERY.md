# Operational Financial Balances — implementation handoff

This is an isolated implementation checkpoint, not a production release. The owner approved the final 24-section scope and dedicated-branch WIP commit/push only. Production financial writes = **0**. Production data unchanged by this task. No merge, Prepare, Prepublish, deployment, accounting opening, activation, inventory initialization or accounting writer was run.

## Source and recovery

- Mezan repository: `AMASI-SA/AMASI-SA`; branch `codex/operational-balance-system-20261005`; base `1ff836914f47181f962b93205ee28cf759f61c0b`.
- Initial remote checkpoint: `e937d9104783313f1aecb807ede60460aafe8e00`, tree `9f6c231c6202fa517353e1ad0f67fe53d15b0101`.
- Native employee application: `AMASI-SA/amasi-mobile`; same dedicated branch name; base `f2d7ed60fb569dd226c6b60e16538064c368f822`.
- Verified native checkpoint: `80b067c6721cbc645da6cd43c3b176ce1436563c`, tree `dd27bcc3a40ac8d8eadf42db073a13b0eceed51f`; remote branch read-back matches and worktree is clean.
- [Complete changed-file manifest](FILES.md) covers both repositories.
- Exact final commit/tree identities are recorded in the final handoff comment on [Issue #1006](https://github.com/AMASI-SA/AMASI-SA/issues/1006). This tracked document cannot contain its own commit SHA. `git rev-parse HEAD` and `git rev-parse 'HEAD^{tree}'` reproduce the identities.
- Original shared Mezan checkout and unrelated work were preserved. No PR was created. Local verification is described below; remote CI is not represented as passed.

## Data model and calculation boundary

`operational_balance_states_v1` contains one owner-scoped aggregate: revision, draft/active/frozen status, immutable openings, fixed start instant, normalized facts/projections, actual movements, explicit supplier returns, request replay results, audit and frozen final snapshot. Compare-and-swap applies a whole transfer, allocations, custody changes and its audit/replay record together. Conflicting request payloads and concurrent over-allocation fail explicitly.

`operational_balance_receipts_v1` stores owner-scoped PNG/JPEG/PDF evidence, up to 5 MiB, with content hash, MIME, name, actor/source/time. Reusing the same content cannot create a second financial effect. New cash, external-person and expense-category metadata uses existing native MZ2 registries; it does not create financial accounting records.

The aggregate rejects growth beyond 12 MiB instead of truncating history. Source reads are capped at 20,000 rows per collection per owner. These are explicit temporary-system limits, not a capacity benchmark. Archive/partition design is required before approaching them.

A 30-second worker refreshes active tenants. New orders after the single opening finish time are included automatically. Existing accounting activation is read as a stop signal: the system freezes its last verified snapshot and stops financial writes. It never invokes the activator. Frozen data timestamp and freeze timestamp remain distinct.

## Native source contracts

| Domain | Read authority |
| --- | --- |
| Employees / custody | `mezan_employees_v2`; salary from `mezan_employee_salary_contracts_v2` |
| Suppliers | `mezan_suppliers_v2`; issued/approved `mezan_supplier_invoices_v2` and covered preparation-piece identities |
| Banks / cash / external people | `mz2_financial_accounts`, `mz2_external_persons_v2` |
| Shipping / drivers / COD | `mz2_shipping_setup_v2`, `store_drivers`, `store_delivery_assignments`, `store_delivery_collections` |
| Payment provider terms | `mz2_provider_fee_policies_v2` exclusively |
| Orders / product costs | canonical payload in `unified_orders`; MZ2 product/option/resource cost contracts and preparation pieces |
| Ads | `mz2_ad_account_bindings_v2`, `mezan_integration_accounts_v2`, native Snapchat/Meta/TikTok/Google daily sources, `mz2_ad_automation_policies_v2`, `mz2_ad_fx_snapshots_v2` |
| Recurring | `operating_recurring_obligations_v2`, `operating_recurring_invoices_v2` |
| Operating expenses | MZ2 daily-movement contract's direct `expense_categories` registry and approved builtin codes |
| Owner / manager | projected native `users` identity: exact owner and their active admin accounts |

The expense registry is physically shared with older consumers. There is no fallback to the Legacy expense tree, and no claim that an untagged registry row's historical origin can be established. Reusing this direct MZ2 contract avoids inventing a duplicate directory. `MezanV2NavigationShellLegacy.jsx` is an existing shell component receiving a navigation link only; it is not a financial source.

## API and authorization

All routes are under `/api/operational-balances`:

| Method | Route | Purpose |
| --- | --- | --- |
| GET | `/context` | Status, permissions and permitted setup issues |
| GET | `/entities/{kind}` | Native choices without technical-ID display fallback |
| POST | `/entities/{cash|external_person|operating_expense}` | Authorized native metadata creation, idempotent |
| POST | `/openings`, `/finish` | Baseline rows and atomic final-row/save/start |
| POST / GET | `/receipts`, `/receipts/{id}` | Evidence upload / authorized retrieval |
| POST / GET | `/movements` | Unified daily transaction / permitted history |
| GET | `/obligations` | Scoped payment choices, including explicit recurring-estimate payment |
| GET | `/reports`, `/audit` | Reports and traceability with separate permission |
| POST | `/supplier-returns` | Accepted supplier return with original invoice fact and evidence |
| POST | `/freeze` | Immutable final snapshot and stop |

There is no new supplier receipt-confirmation API. Existing invoice issuance/approval is read-only evidence. Approval of that invoice later in accounting cannot repeat its operational obligation.

Authentication pins actor and owner, refreshes their active status and checks grants again before persistence/on CAS retry. Mobile page/create/manage/report grants are separate. A daily-only employee cannot access reports, audit, salary allocation details, other employees' movement history or receipts. Movement source is server-derived from session context, not trusted from client input.

## User interfaces

- Mezan 2: opening, daily movements, operational report and authorized accepted supplier return. Opening is one simple page, hides technical fields, clears after save, shows count and saves the final row atomically with finish.
- Opening/daily kinds include the nine original kinds plus custody, operating expenses and owner/manager withdrawal. Custody is separate from salary.
- Daily form uses incoming/outgoing, native entity/account, amount/currency, purpose, optional order, reason/reference/evidence. External parties and expense types can be added by authorized users. No manual commission-percentage screen.
- Native employee app: Expo route, menu/home entry, page/action permissions, same movement endpoint and calculation semantics, image evidence, custody/expense/withdrawal and settlement choices. No duplicate balance engine in the app.
- Both clients retain the exact pending request before submitting, lock an uncertain operation for safe retry, and recover it after reopening. Definitive validation failure allows correction; uncertain outcomes keep their original identity.
- Reporting separates liquidity, receivable/payable, estimates, confirmed, settled and outstanding. Custody funded/spent/returned/remaining and receipts are distinct. Operating expenses exclude owner withdrawals. Hybrid ads show wallet and debt separately. Currency totals remain separate; no invented exchange rates.

## Verification and financial parity

All backend tests ran against private loopback MongoDB and UUID-isolated test databases. They do not import the production server or load its deployment environment. Final commands/results are also in `STATUS.json`.

- Backend: `PYTHONPATH=backend;backend/tests python -m pytest --noconftest <all test_operational_balance*.py> -q` — **104 passed**, exit 0.
- Web: mounted opening/movement/report tests plus existing navigation regression — **34 passed**, exit 0, including lost-response reload recovery, scope isolation, storage failure, definitive rejection and custody/recurring-account reset.
- Mezan local Vite compile uses a loopback API and artifact output directory, not the release dispatcher. Passed. Existing CSS import ordering, large chunks, ineffective dynamic-import and Vite-config warnings remain.
- Native: full TypeScript, scoped ESLint, isolated permission verifier, new behavioral verifier and final local Expo web export. A local Android JS/assets export was also compiled; no APK, EAS, OTA or device deployment was produced. Native evidence is in `evidence/mobile-verification.json`.
- Four unrelated native verifier failures were reproduced on pristine base: supplier invoice services, RTL header, product-cost category completion and component soft stop. They are not presented as passing or silently changed.

| Required scenario | Evidence |
| --- | --- |
| MZ2-only / Legacy rejection | Independent hard runtime collection allowlist exercises every reader; bait Legacy collections must never be touched; legacy-only entity rejected through API |
| Opening / duplicate requests | Immutable baseline, atomic final row + finish, concurrent identical requests and payload conflicts |
| Order states / carriers / COD | Repeated state unchanged; only current carrier estimate; delivered executor frozen; COD separate from shipping cost; transfer conserves cash |
| Supplier invoice | Full/partial native invoice, draft rejection, repeat read and later closure, cancel before/after, accepted return; 8,000 → 3,000 expected + 5,000 confirmed; five of eight covered pieces |
| Invoice value parity | Fractional service allocation 0.33 + 0.34 + 0.33 = 1.00; duplicate piece/cross-invoice coverage rejected |
| Provider fees / settlement / refund | Native effective policy; 1,000 gross − 26 fee = 974 expected; actual settlement fee replaces covered estimate; bank receives net once; requested vs executed refund; later settlement does not repeat refund |
| Bank movements | Reviewed/in-progress native order eligibility, required evidence, reference/content replay rejection, two-account transfer atomic |
| Salary | Next-day start, effective contracts, calendar month/leap February, suspension/hire/end dates, daily immutable identity, full-month rounding conservation |
| Custody | Fund 5,000, expense 500 → custody 4,500; no second bank outflow; return, insufficient balance and concurrent-spend guards |
| Advertising | 200 → 300 → 450 cumulative replacement, complete daily close once, missing source not zero, startup baseline, currency/FX evidence; prepaid/postpaid/hybrid split and separate wallet/debt |
| Expenses / owner withdrawals | Distinct report totals; custody expense vs cash-funded withdrawal; authorized native category creation |
| Recurring | Calendar estimate has no bank effect; explicit partial payment confirms paid portion; later invoice does not double count; native branch/vehicle identity is not mistaken for payee |
| Freeze / authorization | Accounting activation stops/finalizes only operational state; permission revocation, tenant isolation, report/receipt boundaries and stale-refresh guard |

Financial parity here means the listed native-contract and numeric fixture checks. It is not a reconciliation against live production financial data.

## Preview evidence

`evidence/opening-expanded.png`, `daily-final.png`, `custody-expense-form.png`, `report-expanded.png` and `custody-flows-proof.png` are real browser captures of the isolated implementation with synthetic names/data.

The verified local sequence starts with bank 10,000 and cash 3,000; funds custody 5,000; spends 500 from custody; withdraws 2,000 from cash. Final bank 5,000 + cash 1,000 = liquidity 6,000; custody 4,500; operating expense 500; owner withdrawal 2,000 separately. Refreshing/restarting the preview preserves these values.

Local preview: `http://127.0.0.1:5178/operational-preview.html`; local API port 8135; isolated Mongo port 27305. Preview authentication is a synthetic owner in a test-only harness, not the production authentication flow.

## Remaining decisions / explicit verification limits

No new supplier-stage decision is needed: existing invoice issuance is final. No business rule was guessed to conceal incomplete configuration.

1. Native commission/refund/tax/effective policies, ad funding split/FX and partial startup-day evidence must be complete for the real tenant. This task did not inspect or modify production configuration. Missing evidence blocks the affected calculation and is displayed as incomplete.
2. A changed closed ad day is flagged; a documented operational balance correction is available. Automatic consumption of accounting-linked correction approvals is deliberately absent. The original closed-day fact is retained.
3. Real-device UI/image-picker upload, production-authenticated native UAT, device runtime compatibility and full-production load/capacity remain unverified. Local compilation is not a device test.
4. Aggregate/source caps and the four pre-existing native verifier failures require review before a separately authorized rollout. No global CI-green or production-readiness claim is made.
5. Native operational writes default off. A future separately authorized rollout must coordinate backend/client versions, grants and switch activation. No switch, release workflow or production endpoint was activated here. In particular, do not open a native PR blindly: its existing PR workflow can perform an EAS preview update; the current task created no PR.

Next safe action: review this remotely recoverable implementation and its isolated evidence, then authorize any further UAT or rollout separately. Do not run release commands under the present authorization.
