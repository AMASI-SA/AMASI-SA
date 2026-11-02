# OP-UNIFIED-BALANCES-20261102 — combined source checkpoint

Status: DRAFT / COMBINED SOURCE UNDER TEST. Not release-ready or deployed.

## Authority, source and preservation

The owner requested one final combined publication, preserving the supplier/report/payroll/delivery work, provider component fees, latest published customer compensation, product/SAR/status requirements and customer/external-person flows. Code integration does not authorize financial corrections, opening rewrites, historical replay, real refunds or automatic payroll/provider-policy activation.

Latest production read: 3d8720860fb524d729f6c83a8c56c5b4de3a2afb. Integration branch: integration/operational-balances-unified-20261102, Draft PR1687. Parent for this checkpoint: e256b81be642ae9e375cd614677220406a9711d3. No production or original task branch changed.

Preserved inputs:
- Existing #1675/#1677 saved work, latest follow-up df17ef3ff21e2515ed24ec61d5d8019cf9cb2fe5; supplier-only source 861a061fee353c329227fe9f4a0a0f604ea22ad4.
- Provider #1685 source 88c7872072e6e6a149a92153a732ae4c0551ae21, including its unresolved post-settlement safety guard and non-posting statement reviewer.
- Current production #1686 compensation, original-provider restriction and manual bank source behavior.
- Newer native-payment normalization from f25db073815693f72f9fc963c8950c2da99cb4cd and read-only supplier comparator b96627156b522637edd2b6dbf0498a386b1abfff.
- #1678 d5b301506912a481196af295b0812f3759d7a27f contains a source-review workflow only; its runtime requirements are still not implemented by that branch.

The latest b966 source archive from run41445658808, artifact13142781220, ZIP SHA256 b219b69d0600ce73dbd5e99ed4edadf0090f08177d64f975b8854f3c4ab469ea was downloaded and checked against its exact source/tree and per-file Git blobs. Prior supplied local review files were independently hash-checked. Four textual overlaps were reviewed: the current remote payment module and its tests remain byte-identical; source adapter and customer returns were combined by reviewed hunks. No old whole file was substituted for newer source.

The restricted local COD forecast archive is NOT included, retried or uploaded. Its earlier safety restriction remains. This checkpoint combines previously permitted delivery/COD-custody code, not the restricted forecast changes. No alternate upload/execution route was used for that archive.

## Integrated application behavior

Supplier approval after the saved operational cutoff is independent of customer-order age. Approved native invoice components retain stable receipt/obligation identities, original fact payloads, payments, returns and opening balances. A fully invoiced component's final actual cost replaces its estimate: estimated50/actual40 leaves no expected10. Uninvoiced pieces and genuine legacy partial receipts retain their estimates. Full-component provenance is stored on obligations, never retroactively inserted into immutable receipt facts.

Old-order actual delivery has a separate evidence path without importing its old sale/provider payment. Cash custody, earned shipping fees, bank receipts and POS receivables remain distinct. The saved noncash approval transaction/retry checks and report linkage are included. Inclusive Riyadh cutover-day payroll and its owner preview/approval UI are included; existing-account activation remains a separate reviewed action, not performed here.

Provider component fees remain opt-in. The new engine delegates to the existing #1685 component calculator and retains provider_postsettlement_refund_review_required. The statement reviewer remains posting_allowed=false. No actual post-settlement fee-credit implementation is claimed.

Latest native payment aliases, scoped diagnostics, explicit plan propagation and strict dated capture evidence are retained. Missing native capture status/time is not invented from order creation or delivery. New identity conflict checks cannot route inconsistent provider evidence into bank/COD money. In a reduced170/150 order, original-party reads and #1686 compensation remain available, while a new provider refund is blocked when the original capture basis is unavailable. Read accessibility is not proof of refund reconciliation completion.

Supplier account UI, payroll UI, financial-position route/page and driver review are brought together with current cost-exposure and aggregated-alert UI. Full forecast/profit is still unavailable where evidence is incomplete.

## Verification completed before remote CI

Local Python3.13 with isolated hash-checked offline dependencies, synthetic inputs and read-only fake source database. Expanded selection: 488 passed and one actual-Mongo case failed at connection setup, 127.0.0.1:27305 unavailable. No skip, disabled case or mock replacement was used to claim real-Mongo acceptance. The failed environment log/XML is retained. A preceding new test fixture used the wrong month for as_of; that fixture was corrected, not the source validation.

Added four combined regressions: Tamara/Tabby reduced-order reads and missing capture evidence not hiding or duplicating supplier invoices. Ten final-cost cases and the 46 pure supplier-comparison cases remain included. Existing invoice-flow tests that encoded a residual estimate for a fully invoiced single piece now assert zero; the distinct five-of-eight-pieces test still asserts the genuine remaining3000 estimate. No financial guard or assertion about preserved payments/openings was removed.

All candidate Python changes parse successfully; new uploaded blobs were compared to locally tested bytes. The superseded malformed unattached routes blob was never used; correct routes blob is 8338cd9f2d23a59faa452e6544fd5ed904235adc.

The existing Operational Balance Web CI is expanded, not bypassed: explicit supplier-cutover and noncash/real-Mongo modules, five real payroll cases, thirteen real noncash transaction cases, plus supplier/financial-position/payroll UI suites. Existing skip/failure rejection remains and minimum collection checks are increased. Test-only mock/XLSX dependencies are pinned to versions already used in repository workflows. No deployment secrets or financial services are configured. Exact-head CI/artifact readback is required after this checkpoint; previous branch successes are not combined acceptance.

## Complete scope / remaining gates

| Scope | In combined source | Remaining before final acceptance/activation |
|---|---|---|
| Supplier invoices | Independent approval cutoff, exact identities, final-price estimate replacement, account view, pure comparison | Actual owner-scoped consistent-snapshot invoice comparison and opening coverage, explicit approval for missing financial amounts; combined Mongo/UI acceptance |
| Products | Existing production cost exposure retained | Strict per-piece awaiting-review/in-progress eligibility, authoritative SAR for all estimates, received supplier ownership, no duplicated old debts |
| Shipping/COD | Post-cutoff old delivery, actual executor evidence, bank/POS review and cash separation | Current-carrier reassignment/full coverage tests; restricted expected-COD code unresolved; no invented cash |
| Providers | Component fees, native aliases/explicit plan, refund normalization and retained safety guard | Actual dated capture/plan/void/refund ingestion; approved account policies; post-settlement actual fee/tax credits, partial/multi-order settlement linking; gross/net opening reconciliation |
| Customers/external people | Published #1686 compensation and existing loans/exchanges/returns retained | All four debt/cash directions, creation vs repayment, before/after balances, cross-path return dedup and shipping on combined source |
| Payroll/openings | Inclusive cutoff-day rule and reviewed owner UI | Actual opening-coverage review and approval, no silent first-day backfill |
| Warnings | Grouping preserved, payment reasons/refs/actions retained | Resolve genuine source/carrier/cost/fees/ads-start/FX/salary causes, never replace missing evidence with zero |
| Report | Tables, supplier/delivery/account linkage, print/Excel code | Complete same-period forecast/profit authority, mobile/browser/print/download acceptance |
| Release | Prior published customer refund source preserved | Full current-head CI/security/build, latest-production reconciliation, financial activation boundary, fresh v5 A/B, inactive lease, one publication, three verify probes and active=false |

Publishing the changed supplier/delivery reader may recognize previously omitted approved events during normal refresh. Therefore deployment must not be used as a silent financial backfill: complete the read-only differences/opening review and an explicitly approved activation boundary before publication. This candidate is not permission to run the worker on live data.

## Continuity and next action

Prior details remain in PAYMENT-COMPATIBILITY.md and SUPPLIER-RECONCILIATION.md; their older local-only status is superseded by this combined-source checkpoint, not by a readiness claim. The original full history is recoverable from the recorded commits. Draft child1689 was created from the unchanged saved supplier checkpoint for overlap review; it is not a production release and must not be deployed separately.

Next: read this PR's exact new head, inspect full combined CI and downloaded artifacts, fix genuine integration failures without weakening guards, then continue the unresolved scope above. Maintain one final release. Remove the temporary read-only source-review workflow before freezing release source. Do not reuse a previous release intent/artifact or take another conversation's lease.

Owner supplied previous live verified=true/checks=3 for source d0532a057a33776ebe1b40f0d7cbf87a39bf7e4d, deployment3d872086, release rg5-e8d8970b2ab29f4358b856dad1facc6e35e464d1091ad800c0e8df13d844a396. Separate active=false has not been observed here. This is not verification of the new combined code.

Production changed: NO. Live balances, policies, wages, openings, bank/provider APIs, stock, /app, release intent, leases and deployment: untouched.
