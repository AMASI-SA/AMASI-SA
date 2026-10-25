# Supplier editor Preview activation — 2026-10-25

Source: a3520ad0cb0ddaa39678068f12ef50c3a822d476.
Branch: codex/opening-balance-saved-visibility-20261025.
Successful run: 40207115168, workflow head 5acdd6e7926ca8463fb5b85d819ba608cfd4d0cf.
Artifact: 12604155840 (operational-preview-training).

The other task's production lease became inactive before any activation work. No lease was cleared by this task.

Downloaded directly through the authenticated GitHub CLI on the Preview host. Local download stalled and its exact task-owned gh process was stopped; no local artifact was used.

- ZIP SHA256: 6fd0f39765fb99b102bcddc33ed2657d22987a5d90a6352e43f16dbeffff6f59.
- TAR SHA256: 1935d39f96a42602f29f717069513d35a2b152b4adfcc229db29ffdff5f1d66c.
- Manifest SHA256: 90b21949d5d05ffcde4060e0cdebee10b5b3eb9a8c8cd1f5e070f5086d8d6413.
- Verified 4535 manifest files and 4516 exact Git source blobs.
- Frontend index SHA256: 84e64dc5acc459552a1e45e3a808ec4c1d6038a92aad50bf5f1f7595a60ccfca.
- Extracted bytes: 86776351.

New runtime: /app/.worktrees/mz2-supplier-editor-fix-20261025.
Previous runtime retained: /app/.worktrees/mz2-opening-saved-visibility-20261025.
Evidence: new runtime/activation-evidence.json.

The application/backend tree is unchanged between previous and new source. Existing Preview adapter is copied with only runtime path and exact source identity replacements. Same private session signing key and loopback training DB are retained. Current preparation records every collection's count/hash without saving document contents. Supervisor before/after configuration is preserved for rollback.

No Production publish, order/supplier/financial write, Sync, dependency install, or source build in /app.

Activation completed and verified. Only backend/frontend Supervisor command/directory paths changed. Both services are RUNNING; Mongo remained PID 289 without restart. Guard active=false before and after.

- Adapter SHA256: 3a3f252403ba04ab34afd13b20eb21b5d644dba589ed0a363d26b79b9f8a5525.
- Serve SHA256: 57f21aaea1d04701385423d7c8f29c1db13975f52d3d7dccea2c6573dfab4276.
- Supervisor before: 6671b64b3b1874d09b72accd0dda4e9c68691ad21420e551c6b3851165cbbe4c.
- Supervisor after: ab9915affa9791f58a099b6f652690e7e9de22837b36691b6140efb95b969fcc.
- All 42 inspected collections' hashes/counts matched the immediate pre-activation baseline exactly, including auth collections. The user had added two balances since the earlier activation: all seven were preserved. Current opening-state SHA256: 123b441db36bb04e9f845b9f64505efd61399a683cc98aff24716459e95b5ed1.
- Public and loopback HTTP probes passed: /api/health ok=true; /api/preview-auth-policy and /preview-meta.json report exact source a3520ad0cb0ddaa39678068f12ef50c3a822d476 and expected adapter; all HTTP200 application/json. External network denied, automatic workers disabled.
- Both original and candidate immutable manifests passed after activation; unchanged private signing key mode0600 preserved the existing authenticated browser session.
- Browser reload loads /assets/index-Bt9KDlKJ.js. Add Supplier opens its real dialog with supplier fields and an available preparation-service option, and closes normally without Save. No error matching the new asset was recorded. The prior financialData ReferenceError belongs to old asset index-A4BR9l8m.js.
- No missing-service blocker was visible in this current dataset. Save remains disabled with required fields and service selection empty, as expected. Creating a supplier was not executed and is not claimed tested.
- The two newly downloaded compressed archives were removed only after digest and extracted-source verification to reclaim disk space. Extracted bundle, manifests, evidence, prior runtime and rollback configuration are retained. Disk free after verification: 175935488 bytes.

Rollback: after fresh guard inactive and exact-current-config check, restore this runtime's supervisor.before and update only backend/frontend. Keep the current DB and session key; never roll data back.

The local activate_preview.py is a prepared reference helper. Actual actions were executed as equivalent explicit terminal stages and verified by the remote activation-evidence.json; do not rerun prepare against the already activated directory.
