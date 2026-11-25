# Private current-accounting Preview runtime

This is a separate diagnostic runtime for candidate
`9a851854132bd71be922c4aae24b4cc20a546e41`. It does not edit product source, run
`server.py`, deploy the candidate, migrate a database, import production credentials,
or start general application workers.

**Locally prepared and independently reviewed; genuine import still required.**
`coverage-policy.json` pins the reviewed version-2 capture contract (56 scopes in
55 collections) and exact exporter implementation hashes. The contract covers
the owner's eight main views, twelve report tabs and the finite specialized and
drilldown reads below. A core-only capture, older manifest, missing source scope,
changed query/projection, changed exporter or incomplete cursor is rejected. The
runtime remains closed until a complete genuine capture is verified and copied
to the new private database by `import_snapshot.py`. Exact hashes are recorded in
the coverage policy and bundle manifest; they must match the capture receipts.

## Boundaries

- API binds only `127.0.0.1:8002`.
- Mongo is fixed to `127.0.0.1:27019`, replica set `mezan_pr2095_private`, with the
  sole advertised member `127.0.0.1:27019`. Standalone Mongo is rejected: the exact
  source demand worker publishes projections through snapshot transactions.
- Database names must match `mezan_pr2095_YYYYMMDD` with an optional task suffix.
  The existing P01 database/port, default Mongo port, and source database are never
  runtime targets.
- Existing authentication remains at `http://127.0.0.1:8001`. The runtime never
  reads its database, changes its user records, creates sessions, signs tokens,
  changes authentication policy, or substitutes another identity.
- A socket audit hook is installed before imports, including in each exact source
  demand child. API outbound sockets may reach only own Mongo and existing auth;
  worker/children may reach only own Mongo. External DNS and sockets, other local
  database ports, `.env` reads, full-server imports, and unrelated subprocesses
  are denied. API bind resolution is separate from outbound connection permission.

## Authentication and HTTP surface

Only `GET /api/auth/me` and `POST /api/auth/login`, `/refresh`, `/logout` are relayed.
Every relay uses a fresh `httpx.AsyncClient`, `trust_env=False`, no redirects, a
15-second total deadline, finite per-operation deadlines, a 32 KiB request bound,
and a 1 MiB response bound. Original Cookie, Origin and Sec-Fetch-Site headers are
forwarded. Set-Cookie headers remain separate with their original values and
attributes. No shared cookie jar or credential cache exists.

Authorization headers are rejected. Login accepts only the source browser login
fields; native `mobile_client` values and unknown selector fields are rejected.
Native/MFA/mobile-refresh endpoints are not proxied. Cookie envelopes receive a
denial-only client/type check because `/auth/me` does not expose its signed native
marker. This check reads no identity/role claim and grants no authority: the
existing auth service still verifies signatures, expiry, account state and policy.
A normal password login can replace an old cookie through that existing service.

Each protected request asks the existing auth service to validate the actual
browser cookie, requires its stored `role: owner` and exact expected owner ID,
then checks that same copied canonical owner is still active. The product router
reloads its own actor/policy through its original `scope()` logic. No owner
promotion, ID remapping, generated test session, password copy or JWT secret is
used. The saved canonical revision must still match the verified copy.

The existing `BrowserSecurityMiddleware` adds the source API security headers.
An additional exact-origin gate rejects cross-site requests, requires same-origin
browser metadata for POST/DELETE, and denies every unlisted route/method. No CORS,
CSP, cookie or application authentication policy is weakened.

### Main navigation calls checked against source

| Destination | Initial destination read |
| --- | --- |
| Home | `GET /operational-balances/workspace/home` |
| Assets/accounts | `GET /operational-balances/workspace/directory` |
| Movement log | `GET /operational-balances/workspace/movements` |
| People/parties | `GET /operational-balances/workspace/directory` |
| Reviews | Static navigation; no initial review-detail request |
| Financial position | `GET /operational-balances/workspace/position` |
| Reports, all twelve tabs | `GET /operational-balances/workspace/report` |
| Tools | Static navigation plus `GET /operational-balances/automation` |

The shared shell reads `/workspace/bootstrap` and saved `/freshness`. Main log
uses `movements`. Account drilldowns use distinct `resource`, `ledger`, `history`,
and plural `statements` routes. The finite reviewed allowlist also includes
workspace options, selection, context, parties, accounts, resource, forecast,
ledger, statements, history, order issues and supplier invoice detail, plus access
user lists and single-user reads. These routes retain their original handlers and
permission checks. The exported supplier credit-detail source supports the
supplier invoice route; exported disjoint owner/team profiles support access reads.
Customer Exchanges also reads legacy `/movements`. That canonical-state read is
allowed, and the exact stored `denied_permissions` field is preserved for both
owner and team profiles so its original explicit-denial check remains effective.
Recovery proof and ad-correction detail reads require deliberate buttons and are
not part of initial Tools navigation.

`private_http.py` contains the exact additional specialized GET allowlist. Missing
specialized dependencies are not repaired or replaced with fixtures. Do not claim
specialized-page acceptance unless that page's complete source projection has
been verified in the export policy. Access user editing, financial actions,
refresh/recalculation, uploads/imports, provider calls, corrections and every
unlisted GET are denied. The only non-auth mutation permitted is the source
`DELETE /operational-balances/workspace/read-demand/{64-hex-key}` release.

Provider fee navigation mounts the source onboarding setup factory, retaining
only its two GET handlers (`fee-policies`, `fee-policy-candidates`) and the original
fresh actor/accounting permission checks. Its save, preview and commission writes
are not mounted or allowed.

## Verified import and binding contract

The importer creates the mode-0600 runtime config; `runtime.example.json` only
documents its fields. Keep populated config, BSON, manifests containing owner IDs,
and connection information out of the evidence/code branch. No source URI belongs
in this runtime config or environment. The importer has no source URI input and
never connects to a source database.

The exporter directory must contain `COMPLETE.json` binding `manifest.json` by
SHA-256 and no `INCOMPLETE` marker. Source commit, unchanged owner ID, canonical state hash/revision, one
read-only snapshot session with no fallback, and the outside-snapshot final head
recheck must all match. All required semantic scope flags must be true, with an
independently reviewed projection/collection policy. Explicit zero source rows are
valid; absent exports are not a valid empty source.

The importer independently verifies every native BSON file/hash, canonical packed
state and referenced audit page, scope/owner, source query/projection policy, and
duplicate destination `_id` before its first destination write. It compares the
exact pinned semantic contract and exporter implementation receipt, enforces the
nested financial-order output field policy without transforming values, checks
its shape counters, and rejects secret material, private order contact subtrees,
incomplete captures and exceeded bounds. Parent paths are opened through directory
descriptors without following symlinks; captured files must be mode 0600 under a
mode-0700 export directory owned by the current user.

The target must be the sole-member loopback replica and an entirely new task
database. An exclusive import claim is its first write. Original exported raw
BSON bytes are inserted directly in bounded batches. Every imported document is
read back and compared by raw BSON hash, and all declared collection counts,
including explicit zeros, are checked before publication. The source's filtered
order output is copied exactly; its manifest retains a separate pre-filter source
hash. The importer performs no additional projection, merge, reconstruction,
normalization, migration, index installation or financial action.
The `users` owner and team scopes are intentionally disjoint and share one
collection: preserve each BSON `_id`, stored id/role and policy; import once. Never
overwrite one projected document with another or merge fields by guesswork.
Other overlapping collection scopes remain rejected until an explicit verified
merge contract exists. Import must not copy demand jobs/caches or authentication
materials.

Run from this tooling directory with candidate-compatible Python and PyMongo:

```sh
python -B import_snapshot.py \
  --source-root /ABSOLUTE/PRIVATE/source \
  --export-directory /ABSOLUTE/PRIVATE/verified-capture \
  --manifest-sha256 VERIFIED_COMPLETE_MANIFEST_SHA256 \
  --expected-owner-sha256 PREVIOUSLY_VERIFIED_UNCHANGED_OWNER_SHA256 \
  --database-name mezan_pr2095_YYYYMMDD \
  --runtime-config /ABSOLUTE/PRIVATE/runtime.json \
  --preview-origin https://PREVIEW_NAME.preview.emergentagent.com
```

`YYYYMMDD`, uppercase hash placeholders and the Preview name must be replaced with
the actual independently verified task values. The expected owner hash must come
from the known owner identity, not simply be copied from an unverified capture.
No real owner identifier is required on the command line. The importer reads that
unchanged identifier privately from the verified manifest and checks its hash.

Only after verified readback does the importer create the private config and then
insert the exact `__pr2095_runtime_binding` document as its final publication step.
On failure it withdraws only its own matching config and exact binding when
present. Partial data and the exclusive import claim remain; it never drops,
resets or retries over an existing database. If cleanup itself cannot complete,
treat the run as failed and do not launch; the program never reports it ready.
Investigate only that owned task directory/database before selecting a fresh task
name. The importer prints bounded counts, fixed stage/error codes and the public
manifest hash, without documents, IDs, source database names or exception bodies.

The runtime never creates or repairs that marker. Startup compares it exactly,
checks the live replica identity, copied canonical owner and state raw BSON hash,
rejects authentication material in copied users, and compares collection counts.
It also verifies the canonical revision on protected requests. It does not claim
that counts alone prove source projection completeness.

## Start only the two private processes

Use the installed candidate-compatible Python environment (Python 3.11+, FastAPI,
Motor/PyMongo, HTTPX and Uvicorn). `launch.py` verifies the exact clean Git checkout,
requires absence of `backend/.env`, validates export coverage, and re-executes with
only explicit task environment variables. It does not inherit source credentials,
proxy variables, provider configuration or a general application environment.

```sh
python -B launch.py --config /ABSOLUTE/PRIVATE/runtime.json --mode api
python -B launch.py --config /ABSOLUTE/PRIVATE/runtime.json --mode worker
```

The worker runs only exact-source `demand_read_jobs.run(db)`. Its normal source
`demand_read_runner.py` children inherit the same own Mongo target and socket guard;
the general background runner and all other schedulers remain unstarted. Only
task-private read jobs/rows and diagnostic fields are expected to change. Stop
these owned processes through their normal SIGINT/termination handling; do not
touch the original backend, Mongo, or another task's processes.

The existing external Vite config may route `/pr2095/api` to this loopback API,
rewriting only the `/pr2095` prefix. Preserve the original Cookie/Origin/Sec-Fetch
headers; do not replace them with a trusted origin or forged credentials. Keep
the existing frontend security headers. Set the private frontend backend base to
`/pr2095` only once this service is ready. This package changes neither Vite nor
the original auth service.

## Checks and limits

`python -B -m unittest -v test_private_runtime.py` exercises configuration/target
guards, manifest compatibility, real guarded loopback Uvicorn start/stop, inherited
child guards, exact method/path denial, native-mode rejection, cookie isolation,
header preservation and redirect refusal. It uses temporary test fixtures only
inside this tooling directory and makes no Mongo, production, provider or auth
service request. The child fixture tests process inheritance, not report output.
All eleven focused tests passed with exit 0 during implementation.

`python -B -m unittest -v test_import_snapshot.py` adds seven offline import tests.
It uses the sibling `preview-data-tools` package (or `PR2095_DATA_TOOLS`), its
reviewed capture producer and synthetic packed-state/audit fixtures, then an
in-memory destination. The tests verify native Decimal128/Int64/Binary/ObjectId/
Timestamp values and field order through exact raw bytes, owner/team identity
separation, all-file/hash/owner/shape/contract/implementation validation, target
denial, readback failure, lost binding acknowledgement cleanup, partial config
write cleanup and preservation of pre-existing files. All seven passed with
PyMongo 4.18.3. This proves the offline pipeline and publication ordering; it is
not evidence that a genuine dataset has been captured or imported.

All package Python files compile without executing application startup. Runtime
requirements are Python 3.11+, FastAPI, compatible Motor/PyMongo, HTTPX and Uvicorn
plus the exact candidate's normal module dependencies. Importer APIs are available
in PyMongo 4.5; the remote dependency environment still needs its actual factory
import and isolated Mongo test. Do not alter an existing shared environment to
resolve this gate; prepare task-owned dependencies if required.

The actual candidate router factory and Mongo startup still require the remote
candidate dependency environment and the verified genuine dataset. Local syntax
and HTTP tests do not satisfy that runtime gate. An auth relay adds a local round
trip per protected request; source-serving Vite, this private runtime and the
independent demand polling loop must be labelled in any measurements. No measured
speed, accounting correctness, source completeness or deployment approval is
asserted here.
