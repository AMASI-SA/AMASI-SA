# Reviewed independent lifecycle ownership

The first armed worker run at 08ee9f3d... failed before starting tasks because
three append-registered families were absent from the original module filter.
They were not security/migration hooks and must not be dispatched by web.

The replacement classifier uses exact `(module, __qualname__)` start/stop pairs,
requires async functions, rejects unknown/duplicate/unpaired hooks and validates
the complete plan before acquiring the worker claim or starting any task. It
does not catch and ignore classification errors. Existing provider flags, job
leases and payload logic are unchanged. Full names below include `<locals>`
between factory and nested function, as recorded by Python.

| Module / source | Factory | Start / stop | Purpose and control | Owner |
| --- | --- | --- | --- | --- |
| integrations_control_center.ads_auto_sync_scheduler | attach_ads_auto_sync_scheduler | start / stop | Periodic connected-ad-account refresh; MEZAN_ADS_AUTO_SYNC_ENABLED, default true; waits local readiness, cancels/awaits task | worker; rejected previously |
| integrations_control_center.snapchat_capi_purchases | attach_snapchat_capi_purchase_routes | start / stop | CAPI purchase-event outbox loop; MEZAN_SNAPCHAT_CAPI_ENABLED, default false; existing scheduler lease, task cancellation | worker; rejected previously; not enabled by this change |
| snapchat_v2.scheduler | attach_shadow_scheduler | start / stop | Shadow reporting schedule; SNAPCHAT_REPORTING_V2_SHADOW_SCHEDULER_ENABLED, default false; readiness + existing scheduler controls | worker; rejected previously; not enabled by this change |
| advertising_product_watch_scheduler_v3 | attach_advertising_product_watch_scheduler | _start / _stop | Delayed product-watch subprocess launcher, MEZAN_ADVERTISING_PRODUCT_WATCH_ENABLED and existing cadence/resource limits | worker; already classified before |
| campaign_ai_subprocess_scheduler | attach_campaign_ai_subprocess_scheduler | _start_campaign_ai_subprocess_scheduler / _stop_campaign_ai_subprocess_scheduler | Delayed campaign-analysis subprocess launcher, MEZAN_CAMPAIGN_AI_SUBPROCESS_SCHEDULER_ENABLED and existing cadence controls | worker; already classified before |
| product_google_taxonomy_ai_pilot | make_product_google_taxonomy_ai_pilot_router | start_resumable_run_loop / stop_resumable_run_loop | Resume existing pilot jobs with their leases; waits readiness; stop cancels scanner and active child tasks | worker; already classified before |

Registration evidence in the source: ads_auto_sync_scheduler.py:2522-2523,
snapchat_capi_purchases.py:1215-1216, snapchat_v2/scheduler.py:293-294 use append.
The other factories use explicit startup/shutdown decorators. The local AST test
checks these actual declarations without importing their provider/application
modules or executing the callbacks. The successful Linux run logged five active starts (all above except the
product-taxonomy pair, whose source declaration is reviewed but execution is
not established in this run). No inline index creation occurs in the six
start/stop hooks. Existing per-job/on-demand index helpers elsewhere in provider
modules are not mistaken for these lifecycle hooks or silently refactored here.

| Category | Ownership and treatment |
| --- | --- |
| Login/MFA/OTP/passkey/mobile guards, CORS/CSRF, readonly readiness/config cache | Installed directly in each independent web process before ready. Not worker callbacks. No index setup there. |
| Required startup schema and initial empty-DB Owner bootstrap | Explicit fenced migration through independent_schema.py; failed completion prevents readiness; no historical financial cleanup/backfill. |
| Core Qoyod / Plan-B / Salla token maintenance | Explicit armed worker after schema proof and stable exclusive claim. Existing send/idempotency controls retained. |
| Original server.on_startup / on_shutdown | Exact objects excluded from independent lifecycle plan: mixed legacy startup is replaced by explicit roles, not invoked as a worker callback. Original server entrypoint unchanged. |
| Unclassified tasks, future callbacks, missing stop partner, duplicate registration | Fail closed before tasks; never broadly allow a module or silently discard a necessary task. |
| Live provider/accounting actions and feature enablement | Not authorized by callback classification. Synthetic test namespace has no external route or Production credentials. No flag is enabled by this source change. |

Local red/green evidence, standard-library Python only:

`python -B backend/tests/test_independent_callbacks.py`

- Before: extracted original three-module classification reproduces
  `RuntimeError: unclassified scheduler startup callback` for the source-declared
  families (one test error).
- After: five tests PASS, including exact pair acceptance without execution,
  reproduction of the three rejected families, same-module unknown rejection,
  unknown shutdown/missing partner/duplicate rejection, and AST registration
  proof against actual sources.
- Linux runs this same suite with inherited regressions and real role acceptance.
  Function identity test fixtures are for the classifier boundary only; they do
  not substitute the real worker or its guard in Linux acceptance.

The stable worker claim/heartbeat, explicit arming, fence-loss cancellation and
shutdown finalizer remain unchanged. Callback classification happens before
claiming; errors therefore cannot start partial work or create a worker claim.

Final local-only shutdown delta (c344008a): two async tests first reproduced
continued direct work during a stalled stop hook and an unbounded drain. All
seven local tests then passed. Direct tasks/init cancel first; shared drain
has a six-second deadline; failure retains the claim. Linux green evidence
218d55a1 predates this delta. External supervisor escalation remains required
for cancellation-resistant tasks; no claim of final-source Linux success.
