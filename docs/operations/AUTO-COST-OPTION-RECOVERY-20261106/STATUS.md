# Automatic cost option recovery — published and verified

Date: 2026-11-07 (Asia/Riyadh). PR #1786 merged; Emergent deployment a74c051 finished at 17:05.

Source A: d773ec9ed1193c7b57f3a7eab0b4b0217fc855f5
Intent B: 4e26c29b9dfa3b9c6af28f9852328602780b64c7
Production merge: 8947eb0b4ad5b0370efecdaadc044268693122da
Runtime: rg5-0e1a85e3748603bef921f7dec1b7111695bdb6d8f500c464087d0e91dc7996d6

## Behavior

An isolated background worker recovers completely absent Salla item selections for existing orders with configured option costs or bundle definitions. Uses List Order Items, strict native line/product/quantity joins, bounded new/historical scans, durable retries, and component transaction source fencing. It does not copy provider prices/status or invoke financial observers. Unknown selections remain incomplete; partially present nonempty selections are not overwritten. Recovery scans existing orders gradually; this release does not prove that all historical products have completed recovery.

## Evidence

Reviewed-B CI: 658 tests and 345 subtests passed; separate real-Mongo source-fence suite 37 passed; acceptance guard 1 passed. Background worker startup/isolation and Host Node 20/24 clean-clone adapter rehearsals passed. Release Readiness run 42376551071.

Shared /app fast-forward preserved unrelated untracked files. Governed normal yarn build exited 0. Guard prepare/prepublish succeeded for actor auto-cost-options-1786. Published via Emergent management UI.

Live production_release_guard.py verify --url https://mezansalla.com: verified=true, checks=3, matching source/runtime. Live identity flags verified_identity_available, critical_file_hashes_match and frontend_build_verified all true. Final guard status active=false. Production changed: YES. No lease remains.

Final release evidence and continuation ledger: PR #1786 comment 6708092291 (Issue #1006 previously full). The reviewed source and intent remain immutable; this status-only branch is not a deployment candidate.

Next safe action, if a specific product still appears incomplete: read durable recovery outcomes and that order's native selections; do not assume zero, rewrite prices, or re-publish this already verified release.
