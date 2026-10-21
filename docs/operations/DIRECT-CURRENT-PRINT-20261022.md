# Direct current shipping print — WIP

Scope: shipping print only. User deferred list performance, Chrome and biometric changes. Base: 571e1704fc823c681c61b2f31de1db4f5ea5f55a. Branch: codex/direct-current-shipping-20261022.

Changed current external refresh to prefer one fresh order-scoped shipments read. A ready artifact returns immediately; missing fields retain same-shipment detail/tracking recovery. Unsupported order-scoped endpoints retain compatibility reads. Remove duplicate external shipment and order verification cycle, retaining initial completed-order validation and final local completion/identity fence. Courier formatter and COD calculation unchanged.

Tests: python backend/tests/test_direct_print_read_contract.py => 10 PASS. Isolated actual AST functions, synthetic provider and DB boundaries, not full module/HTTP integration. Baseline refresh assertion failed with 6 calls; candidate passed with 2. Foreign order, canceled/return, ambiguous active shipments, partial page, missing URL, tracking fallback, scope fallback and provider failure covered. py_compile and git diff --check passed.

Risk: intentional removal of second provider observation changes race behavior. A provider-only cancellation/replacement after the fresh read may not be observed before handoff; local concurrent changes remain fenced. Existing tests requiring the second observation need explicit review; not waived. Full integration cannot run in current venv because pymongo/motor dependencies are absent. Exact failing user shipment not retested live. No latency seconds, device/physical print guarantee or 100% success claim.

Production changed: no. No merge, release lease, deployment, APK or OTA. PR289 deadline and PR290 Chrome/biometric drafts are separate. PR1455 review guard remains separate. Next: install isolated integration dependencies, run affected print suites, review intentionally changed race contract, verify provider response for reported order, then v5 release rehearsal and rollback proof before deployment. Rollback: revert this task's source commit and deploy through normal v5 protocol.
