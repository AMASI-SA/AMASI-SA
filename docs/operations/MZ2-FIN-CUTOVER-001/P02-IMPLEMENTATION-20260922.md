# P02 shipping/COD implementation — WIP, NOT ACCEPTED

Branch: `chatgpt/mz2-p02-shipping-cod-ledger-20260921`.
Base: PR #1124 at `5292a87a476a140ae8c3c78e88dfba7d8c83f035`.
Do not merge, deploy, activate P02/P03, write live financial data, or advance phase status. Independent review/browser acceptance is outstanding. P01 remains IN_PROGRESS; P02/P03 remain LOCKED; no approved Cutover.

## Owner correction, 22 September 2026

The synthetic SMSA COD brackets are GROSS, including commission VAT: 50..1000 at 1% + 2; >1000..3000 at 2% + 5; >3000 at 3%. VAT must not be added again. Contract-specific dated `commission_vat_inclusive` and `shipping_cost_vat_inclusive` flags are required; never infer another courier's policy from SMSA. Synthetic SMSA/iMile shipping quote 17.25 = 15.00 + 2.25. Synthetic driver fee 20 has no VAT without explicit approved tax evidence. These are test fixtures, not saved merchant settings.

For inclusive pricing: G = Q(amount * rate + fixed), N = Q(G / (1 + VAT/100)), V = G - N. Q is Decimal ROUND_HALF_UP to 0.01. N+V=G exactly. Exclusive pricing: N=Q(quote), V=Q(N*VAT/100), G=N+V.

| COD | Commission net | Commission VAT | Gross |
|---:|---:|---:|---:|
| 49.99 | needs_review | needs_review | needs_review |
| 50.00 | 2.17 | 0.33 | 2.50 |
| 1000.00 | 10.43 | 1.57 | 12.00 |
| 1000.01 | 21.74 | 3.26 | 25.00 |
| 3000.00 | 56.52 | 8.48 | 65.00 |
| 3000.01 | 78.26 | 11.74 | 90.00 |

## Checkpoint 1 — shared calculator

The only calculator is `backend/courier_cod_fee_rules.py`. The historical JSON-numeric facade delegates to the same Decimal engine used by P02. No company-name defaults, database reads, or writes are added to this pure module.

Actual local commands (backend directory, isolated offline dependency environment):
- `python -m unittest discover -s tests -p test_mz2_shipping_calculator.py -v`: 13 PASS, including 20,000 positive inclusive amounts with exact halala identities.
- `python -m pytest --noconftest -q tests/test_courier_cod_fee_tiers_v2.py`: 5 PASS, existing legacy calculator tests.

Contract/recognition/settlement/UI integration is still WIP and is not represented as complete by this checkpoint. No application CI/build/security/CodeQL/release-readiness result is claimed for this new HEAD. Further local contract and regression work must be committed before review; final commands and evidence follow in later checkpoints and Issue #1006.
