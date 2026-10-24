# Instruction notifications release and recovery

Publication is authorized by the user, but has **not** occurred. This document is a recovery plan, not a tested production rollback.

## Verified baseline (2026-10-24)

- Live web health: source `23e3b9008d7a4a7268a38aa66e02711af802df69`, runtime `rg5-90159f4b29ed7e7343ceb313c4a32ebed79b0f354b8b8aacda7318fd9a52eeea`, protocol 5, verified identity/critical hashes/frontend all true. GitHub production head `c045341c7153e563ae2c68c736e862f000c4c01f` contains that release. Recheck immediately before preparing a release.
- Staff: code 58, version 1.0.43, build `bb7621cc-c9de-4700-b667-2a1fde130303`, source `2324c914433c5454c50514a445f29a4fa73a7127`, profile `mezan-web-assembly-pilot`.
- Courier: code 12, version 1.0.11, build `b0a3b7d4-85d4-4c0a-8e32-0873957af4ea`, source `7e78ddf3d932221cbb7079fd0728e2609cc3a03d`, profile `preview`.

## Recovery order

1. For a push transport incident, disable outbound operational push using its configuration flag. Preserve inbox tasks, comments, confirmations, device records, and pending messages; do not clear databases. This does not disable task enforcement or undo customer-service decisions.
2. For client-only defects, retain the server task protocol. Rebuild the last verified good mobile source with the **same package/signing key and a versionCode higher than the installed release**. Both baselines disable OTA. Do not uninstall, erase app data, or claim that distributing an older versionCode is a normal in-place rollback.
3. For backend workflow defects, stop creating new instructions through the affected entry point, preserve all open holds and task evidence, and prefer a narrow forward repair. The old backend does not provide all new creator-only/conversation semantics. Therefore blindly restoring the old code while new tasks remain active is not a safe blanket rollback.
4. A full web/backend code rollback requires reviewing outstanding new-protocol tasks read-only and ensuring that holds remain enforced. Build an inverse source commit on the current production branch, generate a new protocol-v5 intent, complete the release rehearsal, then acquire the release lease and publish. Never reset production history or delete instruction data to make rollback pass.
5. Verify the exact prepared release identity using Release Guard and confirm a newer deployment success. Recovery is not complete at the start of publication.

## Current blockers before first rollout

- Both EAS packages lack FCM v1 assignment and matching GOOGLE_SERVICES_JSON environment configuration. Phone delivery is unverified.
- Integrated source must include the latest production and staff-58 changes, pass review/CI, and produce protocol-v5 release artifacts before deployment.
- A physical-device foreground/background/cold-start notification test is still required. Do not use a real customer cancellation or inventory write as a notification test.

No rollback has been activated; current production and released APKs remain unchanged by this task.
