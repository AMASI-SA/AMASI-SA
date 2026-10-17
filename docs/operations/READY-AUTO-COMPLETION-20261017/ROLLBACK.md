# Ready rollback contingency — not activated

Current Ready deployment commit: bb23e92a88b66bec29ad449e1e7635c44ff2b4f1.
Source: 16a44200f6b8dc79484ebae0010614c6f05b75e3.
Immediate preceding Production: 177f5f6de5523f01c428ed1722cc8e7781c4cb28.
Its tree is 3a6ff57cbc5567aad5fc54ea700feb62f9023723.
This baseline includes the current smart bundle composition release. Do NOT
use the earlier 29cc rollback candidates: those would undo an unrelated release.

Actual-merge-parent rollback branch: codex/ready-merged-rollback-20261017.
Source A_R: 651b2821e863277e35795c37a7d83840c161b7db, parent bb23e92a8.
Source tree: 0c0c8ca050e54612e9c86e0c6d85aa46fec0f7c1.
It restores all preceding Production source bytes while retaining the Ready
predecessor intent for the new rollback build. No data changes are included.
Intent-only B_R: 4dcba49bb57d2b1d19b0b05652f7252908e2ce96.
B_R tree: 335f3a396c5a83b4052becf0723676fb7f73253e.
Runtime: rg5-3f4a8ba8052c436fb8ef52a61645dcba24b0cb5ee1bb8029a4dbd3d9a0c75800.
Source rehearsal39064512416 PASS; final reviewed package rehearsal39065049714 PASS (Backend, Frontend, clean-clone adapter and runtime JSON).

The same source bytes were rehearsed successfully on the prospective B parent:
source d2a1c75796b7dcb0bd56889beb6bb0e177277a53, intent a7d23ca2aa08484cbaeb6226f1fb9550e7a38cd5,
workflow39064082060 PASS (Backend, reproducible Frontend, Linux clean clone and
runtime JSON proof). That result does not replace the actual-parent final check.

## Data boundary
No database migration, record deletion, compensation or inventory reset is part
of rollback. A successful Ready remains committed. Never replay Ready POSTs
because deployment or rollback was interrupted. Persisted dispatch markers,
leases and pending records must survive.

The preceding baseline does not run the new v2 outbox worker and lacks the new
canonical writer fence. Full rollback therefore requires suspending Ready use
during recovery, preserving pending operations and reconciling provider outcomes
before resuming. Prefer a narrow reviewed forward repair that retains fencing
and durable readback. Package rollback does not guarantee uninterrupted pending
completion or reverse provider side effects.

## Execution gates — not executed
1. Inspect the fault and current lease; never clear another owner lease.
2. Fetch Production and verify it still matches the reviewed predecessor. If it
   advanced, rebuild the inverse on the new head; never undo unrelated releases.
3. Require passing final rollback build/rehearsal and preserve source/intent A/B.
4. Merge through the Production branch without squash or rebase of frozen A/B.
5. Check /app inactive lease, clean tracked source, exact fresh origin HEAD;
   preserve env and untracked files, then rehearse the governed adapter.
6. Prepare, prepublish, publish once, verify visible success plus three matching
   release probes, and confirm the matching lease is inactive.
7. Reconcile retained pending outcomes through readback/recovery, without stock
   compensation, record deletion, or blind Ready replay.

No rollback has been activated. Keep the currently working update unless a
specific recovery decision requires changing it. Android APK46 is a separate
upgrade; do not uninstall, clear app data or silently downgrade employee devices.
