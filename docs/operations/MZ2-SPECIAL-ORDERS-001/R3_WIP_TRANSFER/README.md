# R3 recoverable WIP transfer — NOT ready to deploy

This checkpoint preserves 34 exact UTF-8 source changes as a compressed unified diff;
it does not register routes, enable gates, change merchant data, or deploy anything.
Materialization is restricted to the named task branch and exact predecessor bytes.
No source ref is updated by the helper workflow. Full operational completion remains
blocked on supplier/inventory/courier, private labels, sidecar, reporting and lifecycle/UAT.

- Base: b2f317c5bee6f9019fc2e9f24ce5eaea607bd7c9
- Ordered binary chunks: 00.xzpart through 08.xzpart; concatenate in numeric order.
- XZ size: 39652 bytes; SHA256 535f1d5f0e1e853d9d24898450855ced66e1dd5f2703d07e59a5be2a16881823
- Decoded JSON SHA256: f9fd07afe16a38bb44ed4e174576e6e5288336c2bdeb0b04e3fa50fe69865619
- JSON contains exact old/new SHA256 for all 34 sources and the unified patch.
- Materializer verifies path allowlist, predecessor hashes, git apply --check and resulting hashes.

Local actual evidence before this bundle: 192 foundation tests passed (including
real Mongo and replica-set financial/review rollback tests), then six concrete HTTP
and privacy tests passed separately. The two results are not a combined full-lifecycle
run. The branch workflow will run the combined candidate plus the pinned ordinary
baseline independently. Added supplier/inventory recognition code is WIP pending
targeted native-handler tests. Never label this snapshot feature-complete.
