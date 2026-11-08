# Provider completion: evidenced tariff subset

Scope: preserve calculated fees/net for the evidenced paid-order subset even when other orders prevent the full provider balance from being complete. No financial writes, live data refresh, saved tariff changes, opening edits, refund approval, /app change, lease, merge or publication.

Base production: de76aed339be775e2878071c92063d0ab9df06df. Branch: codex/provider-coverage-completion-20261108. Root owns integration/release; the financial-table agent owns presentation integration.

## Implemented

`backend/operational_provider_summary.py` now exposes `known_tariff_gross`, `known_tariff_refunded`, `known_tariff_fees`, `known_tariff_net`, `tariff_known_order_count`, `tariff_unresolved_order_count` in each currency/provider row. Money fields are null if no paid order has a proven tariff; explicit proven zero remains zero. Held/unpriced orders and unpaid orders do not enter the known subtotal. Unresolved order count is null where aggregate tender coverage cannot establish an exact paid-order cohort. Actual settlement fees remain separate and cannot substitute for an unknown tariff. Existing full totals and completeness remain unchanged.

`backend/tests/test_operational_provider_known_tariff_scope.py` covers mixed known/held orders, no-known-evidence nulls, SAR/QAR separation, missing aggregate cohort counts, unpaid orders and fully settled actual-fee evidence with missing tariff.

## Verification

- Initial six regression cases failed because the subset fields were absent; implementation passes all nine final subset cases.
- 127 related tests passed using an owned isolated MongoDB8.0.12 replica set: provider fees/comparison/refund components/net authority/settlement guards/SAR/order payment flow/Emkan bank refunds/Aramex/statement review. No skips or production database access.
- An initial broad test invocation used the suite's unavailable default localhost Mongo endpoint and was stopped after connection failures. Correct isolated replica configuration was then supplied; no source or test was weakened to fix setup.
- 1000 deterministic baseline comparisons preserved all previous monetary values, completeness and resolved identities (new subset fields excluded from comparison).
- `git diff --check` passed.

Commands: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend:backend/tests python -m pytest --noconftest backend/tests/test_operational_provider_known_tariff_scope.py backend/tests/test_operational_provider_fee_comparison.py -q`; broad run adds provider net authority/refund components/components/settlement guard/SAR/order page payment flow/Emkan Aramex release/provider statement review/configured provider fees with `OPERATIONAL_TEST_MONGO_URI` set to a dedicated localhost replica.

## Not yet complete

This subset is not a complete provider total and must not remove incomplete flags. Current live capture timestamp/source gaps still need exact order evidence. No new claim that the remaining seven Tabby cases, other provider cases, or bank reconciliation have been repaired. Root must verify exact-head CI, integrate the financial-table presentation, preserve newer production, and perform governed V5 release plus live acceptance. Existing Tabby scope and Emkan tariffs must not be approved again. Courier clients need no update.
