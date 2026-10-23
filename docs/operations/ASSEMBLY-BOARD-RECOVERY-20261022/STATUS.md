# Assembly board recovery — WIP

Scope: old native in-progress/completed boards; keep the Mezan WebView experimental.

Mobile checkpoint: AMASI-SA/amasi-mobile, codex/assembly-board-recovery-20261022,
a8542ff3cbb6a762d1ba03447c6e2ed391509ba9, PR 294.
Component regression fails on build 54 and passes after the spinner settlement fix.
Explicit search button verified through the actual TSX component harness.
Android 55 candidate: 08ceebcee1d24939b005e731c58db8cc8413af9f, version 1.0.40.
Exact-source Mobile CI 39995945207 and Shipping print latency 39995946067 passed.
Expo build 9191f7b2-fe8b-4485-bfc1-09eae1f6d80a started from that immutable source
with mezan-web-assembly-pilot / production / Android / internal APK / no submission.
Last observed queued; do not start a duplicate build. Final build status is tracked
in mobile PR 294 comments. No new installable APK confirmed yet.

Backend base: 571e1704fc823c681c61b2f31de1db4f5ea5f55a.
Candidate removes sequential per-order reads via existing tenant-scoped get_orders.
Search excludes unrelated workflows before loading details, preserving the existing
5000-workflow window, substring matching, eligibility, sorting and paging.
Tests add a one-bulk-read search contract and retain local/legacy/cancelled cases.
Source checkpoint: 5853bcdf552c6a9ef870e8fee7668745afdb5911, PR 1460.
Python compile and diff checks passed. Local board tests: 52 passed, 13 skipped,
22 subtests passed. Exact-source Review Completion Resumable 39846412019 passed
with real Mongo; 13 other functional/security workflow runs passed except
Security Gate 39846412985, which failed on existing dependency advisories.

Dependency remediation candidate: black 26.10.1, certifi 2026.10.19, Pillow 13.0.0
in both canonical and release backend requirements; Vite 8.2.4 and follow-redirects
1.16.1 with matching registry integrity metadata in the frontend lock. Existing
compatible PostCSS 8.5.26 / Rolldown 1.2.6 resolutions are retained. The governed
runtime test fixture reads the actual Vite declaration. Security Gate now also
runs existing image/PDF compatibility tests under its audited dependency closure.

Verification: strict pip-audit of the updated public requirements exited 0 with
no known vulnerabilities. Local Pillow 13 image/PDF suites: 74 passed, 1 Mongo
skip, initially 2 missing-python-multipart failures; after installing the declared
python-multipart pin, both affected tests passed. Google image controls: 5 passed.
Board/shipping controls with the updated image runtime: 103 passed, 46 Mongo skips,
22 subtests passed. Certifi trust bundle loaded; mirrored backend locks match.
The updated frontend frozen install/audit and exact-source CI remain pending.
An independent read-only dependency candidate review is in progress.

Production changed: no. Release Guard not started. No financial/order writes.
Next: complete candidate dependency review and exact-source CI; prepare source A
and intent-only B using protocol v5. Pillow 13 requires Python >=3.11; the actual
deployed Backend interpreter must be verified before publication (CI/local
interpreter evidence is insufficient). Deployment access
must be restored through the authorized login flow; earlier declined login is not
permission to retry silently. Web session persistence/SSO remains separate pending work.
