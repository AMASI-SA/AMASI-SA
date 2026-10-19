# Completed shipment print recovery — verified source candidate

Production base: e015b913ce950d6e5cf52cc0f1893da94b7b30b4.
Owner authorizes urgent Backend deployment with rollback. Revised acceptance: current local products ready and fresh Salla completed; delivery-outbox history is not a print prerequisite.

Implementation: assembly_print_completion reads current physical/virtual units and exact source-item coverage in a Mongo snapshot, checks fresh Salla identity/status/shipment with GET only, then independently rereads the local evidence before returning the existing saved label. Missing, held, cancelled, archived or duplicate units fail closed. Harmless workflow revision/outbox progress changes do not block. Completion GET, legacy issue and legacy assembly-search inline labels use the read-only recovery. Permissions are rechecked after provider IO. No Ready, stock, financial, provider POST or new-shipment writes are introduced. Queue custody/reprint restrictions remain unchanged.

Fresh local validation on an isolated MongoDB 8 replica set:
- New print and actual route suites: 18 passed, 8 subtests passed, 20.75 seconds.
- Delivery, status transition, v2/legacy readback, legacy print, G47 and saved-label suites: 364 passed, 100 subtests passed, 250.09 seconds.
- Independent final code review: no remaining blocker under revised owner policy. Reviewer did not rerun tests.

The earlier fingerprint-bypass experiment was rejected and removed; it is not this candidate. Preview database had no target order, so it is not Production diagnosis. Live UI established local-ready/completed versus pending-label mismatch, not database repair proof. No emulator print or latency improvement has yet been demonstrated.

Production changed: no. No merge, intent or deployment yet. Next: commit/push this source, open Draft PR, pass final CI, freeze intent-only B, rehearse and prepare rollback, then follow Release Guard prepare/prepublish and exact live verification. Existing unrelated Android/settings/custody work is excluded.

## Deployment proof and failed live acceptance

PR1436 merged as 16e44c3bd5478854bc3d12a9545f05519e61f22d, TREE e8c944d7832bf5a1339acd67e4061f58c780bc36, identical to reviewed intent B24bb523d18f680da7c7b2d2b84ffb5fef6f27f6e. Source A b421a2e0b88787c29fb2c940c2668c6a71ddc530. PR checks: 24 pass, 4 nonapplicable skipped. Real Mongo CI39177246970: 451 tests +265 subtests pass, separate guard1 pass. Clean-clone readiness39177246991 pass.

Rollback branch codex/completed-print-rollback-20261019 HEAD99a656a2d900ca360628f75374fa7faaa435d339 restores prior source excluding intent; readiness39177674441 passed including clean-clone rehearsal. Prepared only, not activated.

/app was fast-forwarded with tracked files clean and untracked deployer-agent-docs preserved. Adapter initially refused generated __pycache__; preserved those directories outside package and reran with PYTHONDONTWRITEBYTECODE=1: exit0. prepare and prepublish passed. Emergent showed newer Deployment finished Oct19 03:12 AM (platform's completion wording). verify --url https://mezansalla.com exited0 with verified:true and exact source/deployment/runtime identity; status active:false. Release rg5-4073a6208fadd57460385dfa8f76d9cc4e0ee940494b3a98087e7fa88918f1ad. Production changed: yes.

Live acceptance FAILED: after reloading the real web assembly screen and searching the owner's affected order, it still showed two of two ready, Salla pending, label pending, evidence-changed reason and disabled print. Explicit status refresh returned to the same view. No successful print, PDF opening or emulator test claimed. No order/Ready/stock/provider-status mutation was performed for this check. Do not claim the incident resolved from CI or deployment identity alone.

Read-only diagnosis limitation: platform database browser returned Application not found; a hidden-input scoped database connection from the remote editor timed out selecting a replica member. No customer data returned or data writes performed. Current readiness/coverage proof must be diagnosed against real order shape before any further code change; operational virtual-item/source coverage is an unproven hypothesis, not a finding. Next: obtain safe scoped read-only actual workflow/source/unit evidence or request/response error evidence, reproduce the mismatch, repair with a real-shape fixture, and independently release only if proven. Keep unrelated Android/custody work separate.
