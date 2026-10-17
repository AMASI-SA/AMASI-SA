# Completed-order print readback — integrated candidate

Scope: restore current saved-label access for legacy completed orders and safe recovery of v2 completion status transitions. APK46 source remains frozen. No Production, live Salla, APK, OTA or financial writes.

Base: bb23e92a88b66bec29ad449e1e7635c44ff2b4f1.
Branch: codex/legacy-completed-label-readback-20261017.
Regression checkpoint: 41f15655f19f774d87092a28774bd8a25e9c4c16 (4 expected failures, 1 control pass on synthetic provider + local real Mongo). Independent coordinator reproduced the same results.

Proven mechanisms:
- The light status reconciler replaces raw status and top-level status but leaves raw status_slug stale; its early skip also prevents repairing this conflict.
- A completed webhook arriving between provider GET and status dispatch is treated as permanently blocked rather than requiring fresh completion readback.
- The component source snapshot/reconciliation window is temporary, but the worker used to stop permanently during that window.
- Pre-outbox completed workflows always appear pending to APK46 even when a current saved label exists.

The legacy and inactive v2 readback paths are integrated. Both require a fresh exact provider order observation and recheck local material, shipment, cancellation, epoch and ownership evidence before returning. An active outbox claim is never bypassed. Healthy reads write no business state. The only recovery write is an explicitly authorized versioned in-progress/completed source-alias repair under the owner transaction, with permissions rechecked inside that transaction. It does not change the workflow, outbox, items, address, stock or financial data.

Candidate 9d74aec417238050201f6b1db995128f8f6a533c is superseded: final review demonstrated that a positive material/address contradiction in the fresh provider response could pass the legacy reader while the unchanged local fence still matched. Seven counterexamples failed for that reason. Legacy and v2 now share the same bounded material comparison. It rejects supplied contradictory items/address/payment/recipient evidence, does not require fields omitted by a light response, and performs no enrichment or writes. The focused regression and omitted-field control passed (2 tests, 7 subtests). The previous CI/artifact is not authority for this new source.

Replacement integrated readback verification: 55 passed, 91 subtests passed, zero skips/failures/errors, exit 0 in 72.18 seconds. Command: `python -m pytest --noconftest backend/tests/test_assembly_completion_legacy_readback.py backend/tests/test_assembly_completion_v2_readback.py backend/tests/test_assembly_completion_readback_routes.py -q --asyncio-mode=auto --tb=line --log-level=ERROR` against the isolated loopback replica set. CI now requires 28 legacy cases and at least 256 total cases; all previous gates remain.

Fresh verification (synthetic provider, isolated real Mongo replica set):
- Baseline regression checkpoint: 4 intended failures, 1 control pass; independently reproduced.
- Integrated affected suites: 421 passed, 1 intentionally unsupported memory-transaction case skipped, 93 subtests passed; exit 0, 388.42 seconds. The corresponding real Mongo case ran. This broad run started before the final embedded-shipment and route tests below.
- Latest legacy/v2/actual route tests after those additions: 53 passed, 84 subtests passed; exit 0, 63.64 seconds. This includes current cancelled or conflicting shipments, returns/old cancelled shipments, claim/lease races, material changes, exact identity, terminal status, transaction rollback, late permissions, timeouts and cancellation.
- Existing saved-label tests: 18 passed within the integrated run.
- Android actual screen/hook/controller compatibility tests passed on frozen APK46 source plus test-only changes, mobile PR 276, commit 7ab293d. Exact old response stays blocked; corrected first GET exposes existing print actions; no resume/Ready/old-issuance call. Synthetic 80/120/60 ms stage delays produced 200 ms to the label action and 260 ms to the enqueue boundary. These are not live/device printing timings; native PDF open was not exercised.
- CI adds the new suites and exact minimum counts, retaining all existing gates. Remote CI and independent candidate review remain required.

One earlier run against the old C: fixture failed setup due to disk exhaustion. It is not accepted product evidence. Subsequent verification used a separate loopback D: fixture without changing or cleaning the old shared database.

Known limits: exact live customer-order cause is unverified. Store courier remains on its existing explicit print path; confirmed completion honestly reports a pending label until that path generates the document. APK46's pending wording and 30-second completion polling remain unchanged. No authentication source, APK, OTA, Production, live provider or financial write was performed in this change.

Backend PR: https://github.com/AMASI-SA/AMASI-SA/pull/1431 (Draft). Mobile test-only PR: https://github.com/AMASI-SA/amasi-mobile/pull/276 (Draft).

Next: push this verified replacement source, inspect fresh CI, and send exact HEAD/TREE to the release coordinator for independent review. The separate one-read-after-status-ACK latency improvement is deferred from this correction. The release coordinator alone owns merge, governed A/B build, lease and deployment. Rollback baseline is bb23 above, preserving already-published fixes; no return to an older update.
