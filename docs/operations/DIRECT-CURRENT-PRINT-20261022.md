# Direct current shipping print — WIP

Scope: shipping print only. User deferred list performance, Chrome and biometric changes. Base: 571e1704fc823c681c61b2f31de1db4f5ea5f55a. Branch: codex/direct-current-shipping-20261022.

Changed current external refresh to prefer one fresh order-scoped shipments read. A ready artifact returns immediately; missing fields retain same-shipment detail/tracking recovery. Unsupported order-scoped endpoints retain compatibility reads. Remove duplicate external shipment and order verification cycle, retaining initial completed-order validation and final local completion/identity fence. Courier formatter and COD calculation unchanged.

Tests: python backend/tests/test_direct_print_read_contract.py => 10 PASS. Isolated actual AST functions, synthetic provider and DB boundaries, not full module/HTTP integration. Baseline refresh assertion failed with 6 calls; candidate passed with 2. Foreign order, canceled/return, ambiguous active shipments, partial page, missing URL, tracking fallback, scope fallback and provider failure covered. py_compile and git diff --check passed.

Risk: intentional removal of second provider observation changes race behavior. A provider-only cancellation/replacement after the fresh read may not be observed before handoff; local concurrent changes remain fenced. Existing tests requiring the second observation need explicit review; not waived. Full integration cannot run in current venv because pymongo/motor dependencies are absent. Exact failing user shipment not retested live. No latency seconds, device/physical print guarantee or 100% success claim.

Production changed: no. No merge, release lease, deployment, APK or OTA. PR289 deadline and PR290 Chrome/biometric drafts are separate. PR1455 review guard remains separate. Next: install isolated integration dependencies, run affected print suites, review intentionally changed race contract, verify provider response for reported order, then v5 release rehearsal and rollback proof before deployment. Rollback: revert this task's source commit and deploy through normal v5 protocol.

## Integration follow-up and separate address fix

Dependencies installed in isolated venv. Actual imported-service suites (legacy print, refresh latency, unified courier) now run: 130 passed, 152 skipped (Mongo unavailable), 22 failed. Failures concern old endpoint ordering, required second observations and fallback behavior under the new direct contract; these remain unresolved and MUST block release. No failing assertions were removed. Current fixture checkpoint represents order-scoped shipment rows and only requests detail fallback when label is missing. 10 direct AST tests still pass. Production changed: no.

Courier district/street/product omission isolated in web PR1457 and mobile PR291, both draft. Web source ecfe5f73388905892cd028800562bda4aed3e0a7; mobile source 70de26a1ab7d195d7d7a6badf8cb219859684333. Four web template cases passed via Node assertions, mobile address script passed, nine external-PDF transport scenarios passed (mocked native APIs). No physical print or device installation verified. Next resolve direct-read regression contract, run CI, then follow release gates. Do not mix deferred patches.
