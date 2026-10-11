# AMASI_READY_SHIPPING_READ_ONLY_RECONCILIATION_FINAL

Decision: independent Draft #1331; not approved for merge or production. No live Salla/carrier request, Android change, financial change, deployment, restart, or release intent was performed.

## Identity and scope

- Branch: `codex/ready-shipping-readonly-reconciliation`.
- BASE: `0b706dee251eb2c2facfdb5e20c40872d78e2158` (#1327), unchanged.
- Runtime checkpoint HEAD: `3039abf196d4f1e7881bc896ec7a00add1e604a0`; TREE: `492e5981a522fd10f046947febc849cbbfa4fd60`.
- Final delivery HEAD/TREE are recorded in the PR and Issue #1006 after this report commit; CI checks out exact PR HEAD and uploads its HEAD/TREE, not an inferred merge tree.
- Android #269: `5680e7b704d98643dde22fd8164d3bd89ed2db7c`, read-only reference.
- Production branch at start: `b41cd9cc36a738a0528dd89ddc7349b31db2a361`; fresh final-work check: `200e66c7a6e60f04bd58fae716b3981ad07ce3d1`. Another TikTok integration advanced that branch. This Draft remains stacked on #1327; no assumption that its tree equals current Production, and no rebase/merge was performed.

## Operation ordering

1. Local Ready and stock consumption retain the existing transactional path. This phase adds no inventory write.
2. Background reconciliation selects due pending/requires_attention operations, acquires shared global/owner slots and the existing per-order CAS claim/120-second lease. New operations receive a permanent operation_id; existing attempt markers are preserved.
3. The worker reads Salla using a dedicated GET-only transport. It cannot POST status, AWB, OAuth refresh, or replay authentication. A missing/expired credential defers for attention.
4. Current source, completion evidence, canonical status, shipping identity, assignment, and status epoch are checked under the existing owner fence. No provider request is inside an automatically retried Mongo transaction.
5. Existing completed orders may recover an existing provable shipment. Only external shipment status `created` is positively printable; pending rows remain pending; shipped/delivered/cancelled/unknown are rejected. Multiple active shipments, changed carrier/ID/AWB, and older provider evidence fail closed.
6. SMSA/iMile PDF bytes are downloaded and validated, then frozen. Provider shipment/order and local fence are checked again before readiness publication. Store courier retains its generated document path with order/barcode/QR and current-carrier checks.
7. Build44 receives an additive short-lived URL under the existing carrier-label route. It returns the exact frozen bytes after another provider read and a final local epoch/identity fence. A fresh refusal for a known document clears old ready/URL/print-data. Initially invalid/expired tokens are refused before binding and cannot identify an owner for cache revocation; they never serve bytes. Token expiry is rechecked after network reads.
8. Final publication remains guarded by claim/lease and workflow CAS. An expired worker cannot publish, release a successor's slot, or clear its result. Timeout or lost ACK never resets status_attempted/awb_attempted or causes a new external POST.

## Load and bounds

Background only: one shared read job at a time, 15-second global cooldown, 60-second owner cooldown, at most 16 candidate documents per scan, 16 automatic read attempts per operation. Backoff is 60 seconds doubling to 3600 seconds. A crashed job/slot becomes eligible after its 120-second lease. Per-job Salla budget is 12 GETs with a 30-second overall worker deadline, plus at most one PDF GET (8-second fetch deadline, included in worker deadline). Thus no more than four background jobs/minute, 48 Salla GETs/minute and four document GETs/minute before durations lower those ceilings. Manual authenticated actions retain request-local budgets; those background ceilings do not apply to all interactive traffic.

Synthetic local Mongo measurements: Ready 81.490/60.619 ms; same-owner writer while provider GET held 7.528 ms; worker cycle 100.878 ms with two fake GETs; cooldown skip 2.342 ms. Explicit 202.352-ms owner hold caused 235.641-ms same-owner writer latency; another owner completed in 14.230 ms. See PERFORMANCE.json and benchmark_ready_shipping_phase1.py. One run, not production percentiles or SLO; PDF latency not measured.

## Document contract

HTTPS/443 only, no credentials/fragments/control characters; DNS addresses must be public and are pinned while original Host/TLS name is retained. Redirects are refused, no provider bearer is forwarded, proxies are disabled. Only HTTP200 with PDF/octet-stream and identity encoding is accepted. Maximum 2MiB/8 pages; PDF signature/EOF and parser validity required; repaired/encrypted documents and missing exact AWB text rejected. Scanned PDFs without extractable AWB remain manual. This is structural plus current-provider identity evidence, not a cryptographic carrier signature.

The 256-bit random capability is stored hashed, bound to owner/order/shipment/AWB/carrier/status/source URL and SHA256 of bytes, expires after 300 seconds, and is checked independently of Mongo TTL cleanup. Response is application/pdf, no-store/private, nosniff, no-referrer; no redirects or HTML conversion. Build44 downloads without API Authorization, hence narrow bearer-capability access rather than changing Android auth. Do not log/disclose capability URLs. The parser runs in-process: byte/page limits bound normal work but are not a hard CPU/RSS sandbox for malicious PDFs; independent review must assess this remaining resource risk.

## Verification and honest boundaries

Local isolated Replica Set `readyPhase1` on loopback27944, Mongo8.0.12; unique disposable fixture databases. Provider and document network boundaries are synthetic. CI additionally forbids non-loopback DNS/socket connections during pytest and uploads JUnit/logs.

- New document/policy/transport/HTTP group: 64 PASS +16 subtests; final route/revoke recheck 48 PASS.
- Worker group: 6 PASS (delayed recovery, two workers, crash/expiry, stale-worker rejection, backoff/exhaustion, terminal writer), real Mongo/CAS. Stock remains16 after initial consumption; both uncertain-attempt markers remain true.
- Existing six shipping suites: 401 PASS across memory/Mongo sweeps; one SKIP is the redundant memory-only rollback variant. Real Mongo rollback counterpart PASS. Subsequent importlib check19 PASS and memory check213 PASS/1sameSKIP.
- Original outbox/canonical aggregate: initial47 PASS/1 FAIL caused only by uppercase AWB mismatch in the new synthetic PDF fixture. Fixture corrected without changing race assertions; exact failed test rerun PASS. Complete group subsequently passed in CI:97 phase1/outbox cases (+25 subtests) on b0106929. Its ready/review group232PASS had two standalone-Mongo skips; the strict gate rejected them. The final workflow now provisions a separate loopback standalone instance, preserving those tests and the no-skip gate. Final exact-HEAD results are in the PR.
- Three original delivered boundaries retain assertions: canonical delivered, zero fake POST, ready false, requires_attention. No test was weakened to accept stale publication.
- Includes completed-order with terminal/unknown shipment, delayed shipment, changed/multiple carrier identity, old/reversed status evidence, expired PDF capability, HTML, redirect/error, wrong AWB, and store courier checks. Original component physical/virtual/stock/review suites are in CI.
- Exact Android269 source exported read-only: Ready27 PASS, API transport15 PASS, shipping state23 PASS, print-flow4 behavior scenarios PASS plus generated store-label check. Backend ASGI tests exercise unchanged Ready/carrier-label payload contract and unauthenticated verified PDF GET. Android source/APK unchanged. Device/printer UAT: NOT RUN.
- Zero external POST is proven for this changed reconciliation/print transport by method-rejection tests (including OAuth/retry cases), captured fake calls and network-disabled CI. Existing assignment/driver writers are intentionally unchanged and are not covered by a repository-wide zero-POST claim. The pre-existing legacy issue enrollment still calls the general best-effort source resync, whose OAuth maintenance is outside the new reconciliation transport; no claim is made that all legacy routes have zero OAuth POST. The background worker and new PDF delivery path never call that resync.

CI results and final source identity are recorded in the PR and Issue #1006 after exact-head execution. Any FAIL remains a release blocker; no green result is implied by this report's presence.

## Before production

Independent code/security/resource review and device/printer UAT remain required. Reconcile with the newly advanced production tree. This phase cannot advance in_progress to completed or create a missing AWB: those require owner/provider action and read-only reconciliation. Unknown/ambiguous/multiple/terminal evidence remains requires_attention. GET observations cannot guarantee that a remote order does not change after the last read or after a PDF reaches a device; downloaded/printed paper cannot be revoked. Remote atomic status/version and idempotency guarantees remain unproven; this phase deliberately enables no write based on an assumed guarantee. Capability expiry, expired auth, unsupported/scanned PDFs and exhausted read budgets require explicit attention. No Production Ready declaration.
