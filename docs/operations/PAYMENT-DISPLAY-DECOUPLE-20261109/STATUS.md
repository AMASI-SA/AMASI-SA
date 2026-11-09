# Payment display / component acknowledgement separation

Base: 18c245748d22615332d10c674812d4362b88ae71
Branch: codex/payment-display-component-decouple-20261109
Scope: order DTO native payment display only. No financial postings, operational component transitions, external requests, production data writes or deployment.

The repository used the financial source readiness gate without a referenced-payment exception; component_pending consequently blanked native_payment_source. The display now uses a dedicated gate that ignores only component acknowledgement, retaining identity, source version, cancellation and authoritative-refresh checks. The mapper still validates native amounts, currency and tender consistency. The stored watermark is never modified. Existing financial consumers retain their original gate.

Verification:
- Before implementation: 9 new regression cases failed because source_balance was absent.
- After implementation: PYTHONPATH=backend:backend/tests /workspace/scratch/81f2839b514c/courier-test-env/bin/python -m pytest backend/tests/test_order_engine_mapper.py backend/tests/test_order_engine_repository.py backend/tests/test_operational_balance_payment_source_current.py backend/tests/test_review_completion_business_snapshot.py -q
- Result: 139 passed, 11 subtests passed. git diff --check passed.
- Paid, unpaid and partial balances remain unchanged through pending -> acknowledged -> pending for mada, Tabby and Tamara. Native Tabby payment_actions with null remaining_amount are covered. Missing/conflicting amounts, currency, identity, stale source, newer status, cancellation and authoritative-refresh remain blocked.
- Mongo-backed order-page payment flow and local assembly suite not completed: no dedicated Mongo instance listening at local default test port. Initial broader invocation encountered connection failures and was interrupted. Local assembly expectation updated for stable payment display; requires CI Mongo verification.
- Issue #1006 read via connector failed (Transport closed). Current production branch freshly fetched; isolated worktree preserves unrelated existing changes.

Next: run Mongo-backed payment-flow and local assembly checks in CI, review patch, then prepare a separately governed release if publishing is requested. No merge, release lease or deployment performed.
