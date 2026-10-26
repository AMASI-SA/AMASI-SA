# Proposed Preview switch — NOT EXECUTED

Owner: task 01a178e8-c96b-7ea2-840d-8b137025159c. Wait for artifact path, digest, staged-runtime verification and explicit coordinated switch instruction.

## Compatibility evidence

Adapter checks this exact startup list before replacing it:

1. snapchat_v2.scheduler.start
2. integrations_control_center.snapchat_capi_purchases.start
3. integrations_control_center.ads_auto_sync_scheduler.start
4. campaign_ai_subprocess_scheduler._start_campaign_ai_subprocess_scheduler
5. advertising_product_watch_scheduler_v3._start
6. server.on_startup

All five scheduler source files match approved source byte for byte. Old and approved server each register on_startup via app.on_event('startup'). Adapter replaces server.app.router.on_startup[:] with [isolated_startup] inside create_app, before returning the ASGI app to Uvicorn. Isolated startup only invokes install_process_local_auth_security(server.db), sets phase preview_isolated_ready/readiness ready and sets process_local_readiness_event. No adapter policy change is indicated by this static evidence. Runtime assertion remains required; never relax it if it fails.

## Proposed sequence

1. Validate artifact digest, safe extraction paths, sizes/free-space reserve and manifest before staging. Preserve existing runtime. Use only approved source 761a8f330cb3bfcadbd430e6ca9a954008dbe9de; exclude PR1551.
2. Prepare new runtime at /root/mezan-preview-supplier-20261026 using a new directory exclusively. Keep training configuration/session key private and byte-identical. No activate call. Rebase only fixed runtime paths and recompute integrity metadata; retain auth hashes and all isolation policies. Do not build or install on Emergent.
3. Validate new wrapper syntax without pycache, all manifest/auth/adapter digests, truthful Preview metadata and API origin. Avoid app import/startup against training DB during this phase. Confirm new frontend launch can use the already installed pinned runtime without package installation.
4. Immediately before any configuration write, recheck Guard inactive, approved HEAD/remote, disk reserve and current service state. Capture exact /etc/supervisor/conf.d/supervisord.conf bytes to private rollback file under new runtime, mode600. Read-back hash must match. Initial observed file: 1222 bytes, SHA256 2633d21f27509783d575c86827f5a0bff46a430ce65c29bba43cd0ca5642ac78. A changed hash requires reinspection, not overwrite.
5. Generate candidate config preserving every other byte/section, including environment and Mongo programs. Replace only command and directory in program:backend and program:frontend, asserting exactly one occurrence of each expected old setting. Both launch the new serve.py with backend/frontend argument. Preserve existing user/log/autorestart settings. Keep candidate mode600 and private; no full config output.
6. With owner instruction, atomically install candidate only if current bytes still equal backup. Run supervisorctl reread, then update only backend and frontend, sequentially. Do not restart Mongo, other workers, or publish Production.
7. Verify both services running, localhost health, Preview metadata source/origin, correct frontend assets, and current signed-in browser session. Validate supplier modal and page loading read-only. No save/test transactions or writer activation. Preserve startup-list assertion and network boundary. Do not claim financial readiness from these checks.

## Rollback

If coordinated switch fails, do not repeat automatically. Preserve error logs and new runtime. Restore exact saved config only if current config still equals our candidate; otherwise stop to avoid clobbering another task. Reread and update only backend/frontend, verify old services and session. No database rollback, deletion, password reset, key regeneration or Production action. Keep both runtime copies and all evidence.

No configuration backup/candidate has yet been created on Emergent; this document is a plan only.
