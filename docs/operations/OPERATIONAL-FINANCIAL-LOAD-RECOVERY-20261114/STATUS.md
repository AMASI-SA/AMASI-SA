# Financial position loading recovery — in progress

The user reported roughly 30 minutes of loading after PR1918 had passed one live attempt. Current production still matches that exact release. A new single visible attempt reproduced HTTP408 during reconciliation and then loaded the old snapshot with a warning. The previous success remains historical evidence, not proof that the recurrence is resolved.

## Direct recurrence evidence

- Visible manual refresh began 2026-11-14T13:43:49.901Z.
- POST refresh returned408 in85,140.921ms; aggregate cancelled after78,625.751ms and reconciliation cancelled after34,098.679ms.
- GET financial-position returned200 in43,589.479ms and displayed old as_of14/11/2026,12:51 Asia/Riyadh with the timeout fallback.
- The runtime excerpt contains another POST499, which is not attributed to this task. Earlier source/native stage events are outside the retained100-line window.
- A separate deterministic frontend reproduction shows repeated hide/show aborts and restarts can keep loading indefinitely; a never-settling promise can strand the shared lane. The exact mechanism on the user's browser is not yet proven.

## Ownership and acceptance

Root owns integration, GitHub, original workspace and publication. The frontend owner handles only the financial page and opt-in visible helper/tests. The backend owner handles only the reconciliation engine, helper and new tests. Dependency setup owns ignored output only. All work is isolated from frozen PR1918 and unrelated original files.

Require baseline-sensitive regressions, accounting/rollback parity, no hidden/offline recalculation or auto-retry loop, explicit bounded loading recovery, complete applicable CI and v5 release protocol, then fresh visible production acceptance. No production change yet.

Issue1006 cannot accept new comments at its known2500-comment limit; this task record and the later PR carry continuation. This handoff branch is documentation only and must not be merged or deployed.
