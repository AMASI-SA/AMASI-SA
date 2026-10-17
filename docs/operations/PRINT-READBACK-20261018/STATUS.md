# Completed-order print readback — WIP checkpoint

Scope: restore current saved-label access for legacy completed orders and safe recovery of v2 completion status transitions. APK46 source remains frozen. No Production, live Salla, APK, OTA or financial writes.

Base: bb23e92a88b66bec29ad449e1e7635c44ff2b4f1.
Branch: codex/legacy-completed-label-readback-20261017.
Regression checkpoint: 41f15655f19f774d87092a28774bd8a25e9c4c16 (4 expected failures, 1 control pass on synthetic provider + local real Mongo). Independent coordinator reproduced the same results.

Proven mechanisms:
- The light status reconciler replaces raw status and top-level status but leaves raw status_slug stale; its early skip also prevents repairing this conflict.
- A completed webhook arriving between provider GET and status dispatch is treated as permanently blocked rather than requiring fresh completion readback.
- The component source snapshot/reconciliation window is temporary, but the worker used to stop permanently during that window.
- Pre-outbox completed workflows always appear pending to APK46 even when a current saved label exists.

Current implementation is still under test/review. A v2 already-blocked recovery helper and versioned status-only repair are in progress. Do not merge or deploy this checkpoint.

Verified so far: first five transition tests PASS after the narrow fix; legacy helper 23/23 and existing saved-label suite 18/18 PASS independently. Expanded integrated tests are running. One run against the old C: Mongo failed fixture setup due to disk exhaustion, not an accepted product result; subsequent verification uses the separate loopback D: fixture.

Next: integrate v2 readback, complete race/permission/terminal tests, run combined regression and Android compatibility checks, then send immutable candidate to release coordinator. Release coordinator alone owns any later lease/deploy.
