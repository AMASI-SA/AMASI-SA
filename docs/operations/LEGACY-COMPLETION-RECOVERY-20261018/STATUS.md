# Legacy assembly completion recovery — WIP

Base: production e015b913ce950d6e5cf52cc0f1893da94b7b30b4.

The owner reports an order approved Ready by an older Android version remains
in progress. Live UI confirmed local assembly complete and shipping frozen;
no raw live outbox record or provider error body was available. Do not assert
the exact live record variant from the UI alone.

Code and two real-Mongo synthetic regressions demonstrate: absent legacy outbox
is falsely described as automatic pending; explicit recovery enrolls read-only
legacy delivery which refuses the in-progress-to-completed transition.

Baseline: 17 delivery/readback-route tests passed in 90.84 seconds. Two new
regressions failed on the base in 18.06 seconds. Missing-operation display
regression passed after the first slice (13.46 seconds); explicit recovery
regression passed after the second slice. Broader current-source tests pending.
Initial baseline attempt could not connect because local Mongo was stopped;
it was interrupted, then the isolated localhost replica set restarted.

Candidate: explicit per-order resume upgrades legacy delivery only after
accepted component input, current ready coverage, consumed units and lifecycle
validation under the operational owner fence. GET never enrolls; no historical
sweep. Worker reads provider status before dispatch; only in_progress can POST,
completed is readback-only, and no AWB POST is introduced. Attempt history is
retained in recovery provenance. Search exposes real recovery/attention state.

Limitations: component plan hash does not prove original address/payment history.
Current shipping is frozen across recovery; original component material must
still match the accepted plan. Missing proof is blocked, not regenerated.
Remote GET-to-POST race remains because provider conditional writes are absent.
Strict full component-unit coverage may reject historical mixed instant routes;
compatibility and negative tests must complete before review/deployment.

Production changed: no. No merge, release intent, deployment, APK, live status
POST, new shipment, or financial/inventory write. Next: finish regression suite,
independent review, inspect the live outbox safely if a read-only surface becomes
available, and establish exact compatibility before any release.
