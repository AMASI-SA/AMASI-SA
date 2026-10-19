# Unified completed-page print contract — implementation checkpoint

Base Production: 0d55bb0ce74df2759e6f9c4786f1b759b29ac0b0. Branch codex/unified-completed-print-20261019.

Owner requested the completed-page black fetch button and current-label print behavior everywhere, and removal of the separate completion-error/refresh/resume panel. Shipment card stays visible and frozen before durable assembly completion; fetch enables after the last product. Printing still requires completed plus current label. Ready transaction and durable worker are unchanged.

Backend: shared prepare_completed_carrier_label uses the existing completed-page refresh guards, independent of historical outbox fingerprint failures. Only order_status_not_completed can enter existing durable resume; other errors never fall back to provider writes. Both completed carrier-label and legacy assembly completion/resume use it. Current authorization rechecked after reads and before resume. No new AWB creator. GET polling remains unchanged.

Web: remove the completion panel; use completed issue/refresh services inside the shipment card. Ambiguous issue switches the next action to refresh only. Polling pauses while explicit action runs. Existing Ready no-repeat protections remain. Android implementation is delegated to the existing Audit task on a separate APK47-based branch; no tracking/custody changes mixed.

Verification: 85 web tests PASS (both ReadyToShipOrders and CompletedFulfillmentOrders suites). Shared helper 9 tests +4 subtests PASS including real isolated Mongo provider reads and old-outbox recovery with no writes. Existing legacy print/boundary/readback plus initial helper:305PASS,1 intentional memory-only transaction SKIP,4subtests in58.73s. Initial run used wrong pytest strict async mode and failed fixture setup; rerun with repository-compatible --asyncio-mode=auto passed. No tests disabled.

Pending: final CI, actual route integration coverage/review, Android integration, current-order live acceptance and actual timing. Prior target294349906 not yet proven recovered; no performance equivalence claimed. This candidate not merged/deployed. Prior PR1439 remains Production. No financial, inventory or Ready writes in this work.
