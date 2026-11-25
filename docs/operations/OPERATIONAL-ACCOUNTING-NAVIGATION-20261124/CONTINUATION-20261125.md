# PR2095 continuation — 25 November 2026

## Result and boundary

Exact candidate `9a851854132bd71be922c4aae24b4cc20a546e41` (tree `436199d239a75910d94d441e9eb47408aea80080`) now renders its real login page in the existing Preview. A private passive measurement panel is visible and its JSON export reports the exact source/module identities and zero samples. The shared native keyboard collision is no longer the blocker: an authenticated independent cloud code-server terminal is usable.

This is source-serving Vite with `NODE_ENV=production` and HMR disabled, not an optimized governed release artifact. No real accounting-route measurements or p95 acceptance have been collected. The existing backend is still the older isolated supplier backend and returns404 for the current workspace bootstrap.

## Ready runtime

- Task directory: `/tmp/mezan-pr2095-preview-20261125`.
- Exact source: `source/`, tracked-clean when prepared and before initial task launch. No backend .env was copied.
- Own toolchain: Node22.23.2/Yarn1.22.22, linux-arm64; receipt `toolchain.json`; all caches/dependencies are scoped to the task directory.
- Current task-owned frontend PID at observation:25822; use `frontend.pid` and verify current process identity before any stop.
- Current config: `preview-tools/runtime.config.mjs`; observer package checkpoint `887e67e8b62bcbe9e86cd7f102451fbb19b7acc1`.
- Log: `frontend.log`; runtime receipt: `frontend-runtime.json`.
- Browser Preview: https://salla-analytics.preview.emergentagent.com/login
- Supervisor configuration and old backend were not changed. This task-owned process is not supervisor-managed and does not survive a host restart.

The original release guard returned active:false immediately before the final restart. Shared /app source remained6323017fbf9ba160c536a94f408507435786412a. Historical PR2118/2123 release owners had already completed and closed their own leases. No lease was cleared by this task.

## Observer integration evidence

The first public asset request was rejected by the diagnostic middleware because the platform forwarded a cluster Preview hostname. A bounded four-entry maximum diagnostic recorded only fixed asset path, normalized method, parsed hostname and allow decision. It identified `salla-analytics.cluster-9.preview.emergentcf.cloud`. The final external config permits exactly that additional static-asset host and disables diagnostic logging. The browser-origin check remains exactly the public Preview hostname; no forwarded header grants access. Product CSP, authentication, backend origin and allowed-host configuration are unchanged.

The browser now displays the Arabic panel and its empty JSON export. LongTask and ResourceTiming support are present. Source and observer hashes match the package; samples0/groups0. This is an integration check, not a timing result.

Local syntax, redaction and percentile examples passed. Independent review also checked fixed asset paths, GET/HEAD only, wrong source/production/foreign/malformed-host rejection, bounded sanitized logging and external-only script/CSS injection. Source selectors have been reviewed but have not run against authenticated candidate accounting pages.

## Concrete data blocker

The old private P01 database has fixture data, not representative active accounting. The other locally accessible database has order volume but no canonical `operational_balance_states_v1`. Neither was changed. Existing authenticated Production GETs confirm the user's genuine active saved state, but those report/context/freshness routes do not export the raw canonical snapshot. Exact-source review found no raw operational-state HTTP export endpoint.

A faithful test copy requires owner-scoped canonical BSON state, referenced immutable audit pages, matching authorized identity and required catalog/source rows, preserving binary packets, revision, timestamps and archive bindings. Do not synthesize that state from projected report JSON. Production does not permit the Preview origin; no CORS relaxation or API proxy was introduced.

The official Emergent UI path Manage → Database → View database opens the database viewer. Preview connects by app name. Production shows Disconnected and requires the Production MongoDB URL. The connection credential has not been read, copied into a tool, or entered. A secure credential step is the remaining prerequisite. An authenticated database viewer is not yet proof that it provides a suitable BSON-preserving export; inspect that capability after connection.

## Resume safely

1. Finish the official secure database connection without exposing the credential in chat, public evidence, frontend variables or code. Keep Preview/backend services intact while checking the canonical export/backup capability.
2. If an authorized faithful snapshot can be obtained, prepare a separate disposable database and reviewed current-accounting read runtime. Preserve the old P01 database and its service. Do not run full current server startup against Production or the P01 data, because startup can migrate/index/start workers.
3. Verify normal Preview authentication, actor/owner scope and all current endpoint contracts. Keep financial refresh/recalculation and provider writes out of the benchmark.
4. Measure cold/warm route observation and response proxy, Long Tasks, pending-read exit and actual data readiness separately across the eight main sections, all12 report tabs and specialized routes. Separate backend queue/build/Mongo diagnostics. The panel is passive and cannot prove request methods or cancellation acknowledgement; obtain those from an authorized network/log surface.
5. Do not claim the p95<500ms target from fewer than20 valid route/cache observations or from the prior synthetic fixture timings. Compare like datasets, runtime types, viewport and cache procedure; label the source-serving runtime clearly.
6. Keep PR2095 Draft, unmerged and undeployed until measured acceptance and explicit release acceptance. Evidence branches are never deployment inputs.

## Restart or remove only this task's frontend

Read the original `/app/scripts/production_release_guard.py status` first and proceed only with active:false. Read `frontend.pid`; verify its /proc cwd is exactly this task's `source/frontend`, its executable is the pinned node_path from `toolchain.json`, and argv is the candidate Vite launcher. Stop only that verified PID, then verify port3000 is free. Never stop an unfamiliar process or change supervisor to make a port available.

To resume the same config, use the verified pinned Node directly (not a long-running toolchain-helper exec lock), cwd `source/frontend`, variables `NODE_ENV=production`, `MEZAN_PREVIEW_PERF_ENABLED=1`, `REACT_APP_BACKEND_URL=https://salla-analytics.preview.emergentagent.com`, and the private config path. Record the new PID/receipt and verify the browser. To remove only instrumentation, stop the same verified process and restart the unchanged candidate Vite config without the observer variable/external config. Preserve the old backend, P01 data and every foreign lease/service.

## Release and continuity

Source checks remain18success/6expected conditional skips/0failure or pending. Existing 1,845 frontend tests,7,605 backend tests and fixture browser scenarios passed at the exact candidate; these do not establish actual-data latency. This task changed Production: no.

STATUS.json is the current structured continuation. Earlier immutable checkpoints preserve the historical blockers and each runtime/tooling milestone. No credentials, customer data or financial records are stored on this public evidence branch.
