# Shipping contract currency repair

## Checkpoint and authority

- Repository: `AMASI-SA/AMASI-SA`.
- Task branch: `codex/shipping-currency-20261105`.
- Verified Production base: `2d73a2a039e16468bbd45523bacf441bff515eb2`.
- Development source only. No merge, release-intent freeze, Release Guard lease,
  deployment, production database write, or provider request was performed.
- The existing print source/intent history and tracked release intent remain
  unchanged. This candidate must receive independent review before integration.
- The exact source checkpoint is the commit containing this status and is
  recorded in the coordinator handoff. Merchant evidence and identifiers remain
  outside Git; all committed regression data is synthetic.

## Behavior

Shipping quotes now carry the approved contract currency. Current shipping
estimates and newly confirmed shipping facts use that currency independently of
the customer order and COD collection. Native source quotes require an explicit
contract currency. Older normalized reducer callers retain their existing
currency fallback; the historical SAR-only quote adapter retains its prior SAR
default for legacy setup records.

A positive COD amount in a different currency cannot select a contract amount
band or supply a percentage calculation. An independently specified fixed COD
fee remains available in the contract currency. A mixed fixed/percentage price
exposes the known fixed part as `cod_fixed_commission` while the whole fee stays
incomplete. A fixed price inside an amount-dependent band remains unavailable
without a basis for selecting that band. No FX rate or conversion authority is
introduced by this patch.

An existing unconfirmed, unallocated estimate is replaced using its existing
obligation identity. Saved confirmed fees, fee facts, and payment allocations
cannot be silently relabelled from a current quote. Conflicting or missing
recorded currency produces an explicit shipping issue and preserves the saved
fees and movements. New fee facts carry their currency. Shipping guards index
their related keys once instead of scanning all obligations/facts per order.

A later complete COD commission includes any already confirmed fixed part:
only its remaining amount is newly confirmed, preserving the canonical total
and rounding. An old complete commission does not acquire a second fixed fee
from a later incomplete source. Superseded unconfirmed/unallocated fixed
estimates are cleared even when delivery and the complete commission arrive
together. An allocated fixed estimate requires review instead of being cleared.

## Delivery boundary

For **post-start external courier orders**, the delivery event retains the
consistent customer currency proved by the canonical source. The event must
match the order currency; the fee retains the independently approved contract
currency. Real `collect_sources` tests cover both prepaid and COD delivery,
including a fresh source read and deterministic replay.

The **native driver producer remains SAR-only**. **Pre-start foreign delivery
remains outside the existing SAR-only historical fee approval contract.** The
patch does not broaden historical fee approvals or reconstruct foreign opening
balances. Conflicting customer currency evidence, order/event currency
disagreement, and native foreign-currency driver evidence are rejected.

## Verification

The original defect was reproduced through `collect_sources` and `reconcile`:
a synthetic KWD sale with a SAR contract produced a shipping estimate labelled
KWD. The regression failed before implementation. Independent review also
identified and reproduced the actual courier delivery rejection and the
open-partial to delivered-complete duplicate estimate; both were then repaired.

The initial source checkpoint `3b14b1b1ba0ecc3b8672efe8e92a59a00ae7e934`
passed 332 local cases. Independent review then reproduced missed SAR pooled
payouts, an incorrectly labelled native-overlap candidate, a post-start
historical approval borrowing the sale currency, and rejection of a legacy
SAR return contract. The additive correction keeps this first checkpoint
in history.

A current courier fee is checked against payouts and known payable openings
in its own currency. Fee holds keep independently validated delivery and COD
custody evidence. The nonposting review row and the one-order historical
approval candidate take their amount and currency from the same fee source.
The historical approval surface remains limited to SAR fees; pre-start
admission and native driver restrictions remain unchanged. A replay confirms
the approved fee once in SAR and does not create a cash movement.

The existing SAR-only customer return path accepts historical domestic rates
that omitted currency, but rejects an explicit foreign rate or confirmed base
fee. The only adapters allowed to supply this legacy SAR default are the
already SAR-scoped return and open-cost readers. Current general-purpose
shipping quotes continue to require an explicit contract currency.

Fresh final local verification: **342 passed, 3 skipped, 1 deselected**, exit 0.
This includes 37 shipping-currency cases and the neighboring source, reducer,
delivery, tariff, verified-fee, open-cost, completeness, and financial-position
contracts. `git diff --check` passed, exit 0.

```bash
PYTHONPATH=.:backend:backend/tests OPERATIONAL_TEST_MONGO_URI= python -B -m pytest --noconftest -q \
  backend/tests/test_operational_balance_shipping_currency.py \
  backend/tests/test_operational_balance_engine.py \
  backend/tests/test_operational_balance_engine_fixes.py \
  backend/tests/test_operational_balance_source_fixes.py \
  backend/tests/test_operational_balance_delivery_cutover.py \
  backend/tests/test_operational_balance_delivery_evidence.py \
  backend/tests/test_operational_balance_courier_tariffs.py \
  backend/tests/test_operational_balance_verified_shipping_fees.py \
  backend/tests/test_operational_open_cost_exposure.py \
  backend/tests/test_operational_balance_cost_completeness.py \
  backend/tests/test_operational_balance_financial_position.py \
  -k 'not test_real_refresh_preserves_old_delivery_custody_and_bank_settlement_once'
```

The three skipped tariff approval cases require an explicit isolated Mongo
replica set. The deselected real-refresh case requires the same environment;
an initial attempt with an empty test URI correctly failed its localhost guard.
No database was opened for this local verification. A temporary indentation
error and the legacy historical setup currency-default regression were caught
during development and resolved before the final successful run. Neither skips
nor exclusions count as accepted Mongo integration evidence.

The new test filename is covered by the existing
`backend/tests/test_operational_balance*.py` CI selection. No workflow was
changed. Current-source CI and independent review remain pending.

## Next safe action

Review this exact task-branch diff and source checkpoint. Complete the existing
isolated-Mongo/CI gates before any governed release integration. Coordinate with
the owner of the print release; do not amend its frozen A/B history. A future
deployment requires its own reviewed source and v5 intent. This checkpoint is
not a claim that the live report has been corrected.
