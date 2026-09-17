# Preview PayPal discovery test — 2026-09-17

User authorized a synthetic Excel order and settlement test in isolated Preview. No Production or PayPal external action.

## Executed
- Created two Excel fixtures with synthetic-only labels.
- Uploaded PREVIEW-PAYPAL-ORDER-20260917.xlsx through Preview /upload.
- Import UI: completed1/1, created1, updated0, failed0.
- OrdersV2 shows PREVIEW-PAYPAL-20260917-001, customer test label, PayPal,100.00SAR, pending review. This proves Excel ingestion/display only, not native Salla webhook handling.
- Fixture has no structured skus_json; UI shows0item lines. Not evidence of product-import fidelity.
- Reopened Mezan2 settlements after import. Creation selector remains Salla,Tamara,Tabby,Emkan; filter same plusAll. PayPal did not appear automatically.

## Settlement fixture
PREVIEW-PAYPAL-SETTLEMENT-20260917.xlsx uses illustrative gross100,fee3,net97SAR; net formula=Gross-Fee. This is a generic synthetic fixture, not an official PayPal export or real fee schedule. No PayPal provider option exists, so the settlement file was NOT uploaded under a wrong provider and no draft was created.

## Result and next action
Discovery gap reproduced. PayPal label survives order import but new settlement providers are not auto-created. Code corroboration: accounting_settlement_service.PROVIDERS and canonical_provider enumerate four providers. Add an extensible setup path and explicitly supported statement mapping before continuing the settlement test; unknown provider must remain unconfigured rather than silently mapped to Salla. No posting/reversal or real settlement edit. Production unchanged. Existing P01 gates remain open.
