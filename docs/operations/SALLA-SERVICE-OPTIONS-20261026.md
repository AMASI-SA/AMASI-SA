# Salla service customer fields and extra prices

Owner request: repair and publish the Mezan creation flow, support the reference
hoodie's adult option surcharge of SAR10, then create the existing pink Kitty
hoodie draft with all six reference fields. Do not duplicate the draft/product.

Base: 3a23b587a218f2b190cf1913e3b0b1d22c175cbd.
Task branch: fix/salla-service-options-pricing.
Earlier completed change: PR1539, merged56560cf68eb08fa8ef5de9780701722c283fbc95;
its final comments are the continuation ledger because Issue1006 reached its
comment limit. That deployed radio-type fix and completed shawl973697601 remain
untouched.

## Current evidence

- Live existing hoodie draft AMASI-KITTY-PINK-2026: six fields, type service,
  SAR139, rejected by Salla POST/products with422 options, message prefix
  `لقد تجاوزت الحد`. The integration truncated the original JSON at200bytes.
- Fresh Salla SKU lookup before edits returned zero products. No new attempt.
- Inline field expansion is the current failure boundary. Proposed correction
  uses the documented POST/products/{product}/options endpoint for service forms
  above three fields. The exact provider limit remains unconfirmed until live
  acceptance; do not claim the provider cause is fully proven from a test fake.
- Service is created hidden with zero quantity, option/value IDs are read back,
  conditions linked, extra prices verified, then inventory/status finalized.
  Applied-option timeout resumes by the persisted product ID without recreation.
- Optional value_prices array preserves old drafts; UI accepts per-value
  nonnegative surcharge, preview shows the surcharge and conditions keep using
  the original value. Deleting a value removes its price at the same index.
- Focused backend:47 passed, exit0. New six-option lifecycle, applied-timeout
  retry, ignored-price rejection and invalid-price cases included. Existing39
  rule/workflow tests remain green.
- Frontend test added; not yet executed locally (dependencies absent). Full CI,
  source A/intent-only B, clean-clone rehearsal, merge and governed publication
  remain required. No production source/runtime changed by this task yet.

## Next

Push source checkpoint and create PR; run CI/frontend tests; review exact diff.
Freeze reviewed intent-only B after source readiness; verify exact B in isolated
rehearsal, then coordinate a fresh inactive release slot. Never clear another
owner's lease. Publish once, verify3, close own lease. Refresh existing draft,
set adult surcharge10/kids0, preview/approve/create, verify all six customer
fields and conditions and prices in Salla. Complete generated square images,
description/category/SEO and storefront acceptance, then return actual URL.

Images/video already completed and delivered. User is also adding images to
Salla manually; read product media before uploading to prevent duplicates.
