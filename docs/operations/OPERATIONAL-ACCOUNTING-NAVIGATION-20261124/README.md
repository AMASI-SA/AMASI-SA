# Operational accounting navigation: Preview handoff

This evidence branch records work on Issue 2094 / Draft PR 2095. It is not a
deployment branch or a frozen release intent. The source candidate remains
`9a851854132bd71be922c4aae24b4cc20a546e41` on production parent
`c6d05c7814e27e2f22a8c82b5bfe4cf177c17fbe`.

## Confirmed source verification

All 24 exact-source checks completed: 18 successes, 6 conditional skips, 0 failures.
The operational frontend passed 115 suites / 1,845 tests. The operational backend
passed 7,605 tests + 151 subtests, then 106 demand-read cases, then 16 shipping cases
+ 18 subtests. See the [workflow run](https://github.com/AMASI-SA/AMASI-SA/actions/runs/45478681136).

The current synthetic browser job measured 48.938 ms, 57.148 ms and 57.597 ms for
click-to-next-visible-heading at three viewport sizes. It also proves demand
release and cessation of polling when leaving a pending report. These are
fixture samples. They do not establish real Preview p95, initial data readiness
or the user's Production improvement. The test does not enforce the 500 ms budget.

## Actual host access and current coordination

The saved TinyFish admin session reached a login wall. A separate, already-open
native Chromium code-server session is usable and connects to the expected
Emergent host. This task opened a separate terminal there. Reuse that surface;
do not request another login solely because the TinyFish session is expired.

Original guard status was read directly and was active for
`chatgpt-operational-cost-report-20261124-pr2118`, deployment
`c6d05c7814e27e2f22a8c82b5bfe4cf177c17fbe`. PR 2118's owner is publishing its
separately authorized release. Preserve that lease. No shared source, service,
database, lease, or publication changes have been made by PR 2095.

Direct supervisor status: frontend FATAL; backend RUNNING. The preserved private
October launcher uses an older worktree's Vite to serve an October supplier
bundle. Restoring that Vite alone would restore an old Preview, not PR 2095.

## Recovery decisions

1. Use the bounded read-only `inspect_preview_host.py` to establish current
   paths, service state, capacity and unauthenticated local backend API routing.
   Frontend HTTP requests are deferred until its proxy configuration is verified.
   Output excludes environment secrets, credentials and database rows. The
   original guard status command may use its normal advisory operation lock;
   the inspection does not change or clear a release lease.
2. Prepare exact candidate source and its own frozen dependencies in an isolated
   directory on the verified roomy volume. Avoid shared `/app` artifacts and
   stale dependency symlinks.
3. Read and preserve the private backend's authentication, worker and outbound
   isolation before considering a current-source backend. The existing P01
   Mongo on port 27018 and its data are not disposable.
4. Verify the original guard is inactive immediately before any shared service
   switch. Do not clear or finish PR 2118's lease on its owner's behalf.
5. Supply the actual reviewed Preview API origin explicitly. Tracked Vite config
   disables env-file loading and has no API proxy; `src/lib/api.js` has no missing
   origin fallback. HTTP 200 for SPA HTML alone is not an API proof.
6. The supported current-source frontend entry is `start:dev`. Normal `start`
   validates governed output, and normal `build` enters the production adapter.
   Do not use either for an unfrozen source Preview.
7. Do not leave a Preview server running under the release toolchain helper's
   `exec`: it holds the shared toolchain lock throughout the child process.
   Use bounded toolchain preparation, then the verified Node binary and the
   candidate's own Vite entry for the long-running server.

## Remaining acceptance

Verify candidate identity and actual authenticated, isolated API compatibility.
Measure cold/warm click-to-URL, click-to-interactive, long tasks and p50/p95 across
all operational accounting sections and all 12 report tabs, including departure
during pending reads. Target route-interactivity p95 below 500 ms. Record data size
and backend queue/build/Mongo timings separately; do not equate a loading badge
with a blocked click handler or infer performance from absent metrics.

Draft PR 2095 stays unmerged and undeployed until measured Preview acceptance and
the user's explicit release acceptance. A later authorized release still needs
a newly frozen A/B intent, full governed rehearsal and the original release
protocol. Historical PR 2076 commands must not be reused.
