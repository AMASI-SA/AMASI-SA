# B2 deployment: platform success and guard verification established

The owner reported Publish 100 and explicitly confirmed `Deployment Succeeded`.
The owner-supplied `/app` verify receipt reports `verified=true`, three consecutive
checks, the exact A2 source/release identity, B2-equivalent deployment commit,
Backend runtime/control source manifests, and exact Frontend artifact verification.
A subsequent owner-supplied `status` receipt reports `active=false`.

- Deployment commit: `78dcf31af73581ceba3677c464c657a0b9b2c4fc`
- Candidate B2: `d6c0553ae6a99a85d52b03871b7bf64024180514`
- Deployment/B2 TREE: `806e935c8ecc3268f98096cd2b477050d235c99e`
- Source A2: `79f307f6ff69ba658958ebcdce03341b39a55939`
- Release ID: `rg5-8f098823ae66cfa6c93a7d8effc3a87b0515796f8ef49eba4e4bb94e53696ff6`
- Rollback: `83363097d48e034dc7140a60c290efc684e1ffde`

## Evidence and limits

| Requirement | Evidence | Result |
| --- | --- | --- |
| Platform success | PLATFORM-SUCCESS-OWNER-RECEIPT.json | PASS, owner UI attestation; no direct browser observation |
| Exact release and three guard probes | APP-VERIFY-OWNER-RECEIPT.json | PASS, actual owner-run `/app` command output |
| Lease closed | APP-POSTVERIFY-STATUS-OWNER-RECEIPT.json | PASS, active=false |
| Health/live/ready | POSTDEPLOY-PUBLIC-PROBES.json | Direct unauthenticated GETs each HTTP200 |
| Frontend/build-meta/asset bytes and MIME | Guard receipt, canonical and cache-busted probes | PASS through `/app` guard; earlier Windows observer 403 retained as channel limitation |
| Unauthenticated Auth boundary | Direct GET /api/auth/me | Expected HTTP401; not a positive login test |
| Authenticated Auth/Mongo, MZ2, accounting and current-carrier reads | Pending owner-session GET-only smoke | NOT YET EXECUTED |
| Actual platform deployment timestamp | Not supplied | PENDING; replica boots must not be relabeled deployment time |
| Cloud Build adapter transcript | Not supplied | PENDING; local rehearsal does not establish actual Cloud Build argv/cwd |

Observed replica boots are `2026-10-02T19:31:49.232435+00:00` and
`2026-10-02T19:34:06.572286+00:00`. The platform-displayed `8f284e6` is retained
only as an unclassified platform identifier, not a Git SHA.

## Next safe action

Open `https://mezansalla.com` in a browser with the owner's normal authenticated
session. Run `postdeploy_owner_readonly.js` once in that page's developer console
and return only the generated JSON. Do not paste credentials or raw API records
into the conversation. The script uses the existing session only for same-origin
GET requests, disables redirects, checks the release identity and paused control
state, and stops without retry on errors. It makes no login, refresh, sync,
posting, activation or mutation requests. It compares existing list/detail
shipping projections without generating a new shipping event.

Source review established that the selected endpoints read existing records.
Authenticated `/api/auth/me` checks the current user through Mongo; health and
readiness alone do not query Mongo. Native account reads use
`mz2_financial_accounts`; the control endpoint reads `mz2_atomic_owners` and
pending ingress count. Empty data or absent current-carrier facts remain explicit
limitations. List/detail differences are reported without assuming a regression
because a concurrent operational update could also explain them.

The console helper passed `node --check` and local mocked sequence checks for
success, wrong release, HTTP401 and unpaused control. Those checks validate only
the helper and are **not** Production smoke evidence.

Do not repeat prepare, prepublish, publish, verify, or lease creation. Preserve
the sanitized adapter portion of the actual Cloud Build log and the platform
timestamp if the platform exposes them.

Production software changed; task Production financial writes remain **0**.
No write-control change was made by this task. A persisted before/after Production
financial-data snapshot was not captured by this verification, so this is an
action-scope statement, not a database-wide forensic zero-write assertion.
Opening, inventory initialization, Activation, P08, backfill and financial
schedules remain **NO**. Full Business UAT remains **NOT PASS**.
The final all-requested-checks-complete declaration remains pending the checks above.
