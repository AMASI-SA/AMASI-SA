Preview activation completed and verified, 2026-10-25. Final evidence is appended below.
Source 1f61d4d416d9f47858e4e7cfab54fd572a702e6d, run40199686191 successful.
Fresh tests: 62 frontend and 2 backend passed using existing dependencies outside /app.
Artifact12601356351 ZIP SHA256887b9feb3a7403cc89e8d68a6c618d527d5e89ea09c432300e08391420a73d1a.
Downloaded on remote after local download connection failed. TAR SHA2561f41113219b97b6600adb0af3922e80e064b9dac7247eb0470f6b066288b2b71.
New persistent path /app/.worktrees/mz2-opening-saved-visibility-20261025.
Verified4534 manifest files and4515 source Git blobs against pinned1f61d4d41.
Manifest SHA256d6d506ee4c24ee7344aecc31d454a359b35396c2bdc9ee447e2a13e99577bea6.
Auth source hashes unchanged; existing session signing key copied privately unchanged. No base source or old bundle edited. Same loopback Mongo27018 replicaSetp01acceptance and DBmz2_uat_1128_20260921.
Baseline operational state:1 document,5 openings; SHA2561e1aa9aa5f79eca4dbbb8faf5f9a6713f057b54200cd5c83d047bbdf807c10c4. Receipts/claims empty. Browser existing training session visible with5 openings before change.
New adapter SHA25679651a8253ab1a9ea78561b2ced91f2911dd6c897350d242c782e78a05cd40b6; serve SHA256fe8692078d7bfca2f0af8705de019c74ca63c0b487e97ef13d113038d1bd8bd4.
Supervisor backup saved privately in new path as supervisor.before/after. Before SHA256f870dec3cc79f924995473d09418c1ba0ba6ee8ec28635938be9cb5e32ad9b0e; after6671b64b3b1874d09b72accd0dda4e9c68691ad21420e551c6b3851165cbbe4c.
Guard active=false checked immediately before configuration switch. Only4 path occurrences in backend/frontend command/directory changed; other settings preserved. Reread requested, service update/health/UI/data preservation proof still pending. No production deployment, financial data mutation, Salla sync or dependency installation.

Final verification:
- supervisorctl update backend frontend completed. Both services RUNNING (backend PID 6665, frontend PID 6688); existing Mongo PID 289 remained RUNNING.
- Public and loopback /api/health, /api/preview-auth-policy and /preview-meta.json returned HTTP 200. Health ok=true. Policy and frontend meta both identify exact source 1f61d4d416d9f47858e4e7cfab54fd572a702e6d. Adapter hash matches the value above.
- External network remains denied, automatic workers remain disabled. No Production publish or financial/material writer activation.
- Read-only database comparison passed: operational_balance_states_v1, operational_balance_receipts_v1 and operational_balance_operation_claims_v1 hashes/counts exactly unchanged. Five opening balances preserved.
- Existing and new bundle manifests both reverified after activation. Session signing key unchanged, private mode 0600. Production guard active=false after activation.
- Existing browser session survived reload. /operational-balances visibly shows the five existing entries in the new saved-balances table, each with the saved checkmark. The payment-platform entity selector shows saved markers for the two previously saved entities; unsaved entities have no marker.
- No Save or Save-and-finish action was triggered. Retention of selected kind after save is covered by automated tests, not a live financial write.
- Remote evidence: /app/.worktrees/mz2-opening-saved-visibility-20261025/activation-evidence.json (activated=true, post_activation_data_unchanged=true, HTTP observations and digests).
- Rollback inputs preserved: supervisor.before and original /app/.worktrees/mz2-training-persistent-20261025 runtime. Before any rollback, confirm no production lease and current config still matches supervisor.after; restore only the saved supervisor configuration and update backend/frontend. Do not replace or restore the database.
- Disk free after activation: 528400384 bytes. No dependency install or source build in /app.
