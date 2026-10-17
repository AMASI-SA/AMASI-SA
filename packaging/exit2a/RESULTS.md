# EXIT-2A — evidence and acceptance status

This is a rehearsal package, not a deploy-ready replacement for Emergent.
No Production data was used. No migration, real restore, release intent/lease,
deployment, provider request, DNS change or paid resource was authorized.

## Immutable source and scope

Baseline `1de6118484ac4fe1d0981e230618dbb573d8c58c` was rechecked against
`origin/hotfix/prod-snap-meta-final` before creating the independent branch
`codex/exit-2a-portable-linux-package`.
Local path: `C:/Users/amasi/.codex/visualizations/2026/09/05/01a073a3-3dd0-7901-82c1-556ca731163b/EXIT-2A/checkout`.

Only existing application file changed: `backend/bank_transfer_review_routes.py`.
Its router factory scheduled a Motor `create_index` at import time. The new
explicit rehearsal condition suppresses only this call; ordinary behavior and
index definition remain unchanged. Server, frontend, Release Intent and Guard
files remain byte-identical to baseline. No #1003/#1013 paths were edited.

New files: baseline/runtime workflows; this directory's Dockerfile and scoped
Docker ignore, portable manifest, entrypoint, rehearsal boundary, source audit,
unit/real integration acceptance and Linux harness, documentation.

## Baseline Linux build — verified

[Run 33997731256](https://github.com/AMASI-SA/AMASI-SA/actions/runs/33997731256),
job 101391077250, Ubuntu 24.04.4 x86_64/Python3.11.16. Workflow explicitly
checked out the unchanged baseline. All 687 reviewed frontend members matched;
all tracked backend/frontend/scripts/release inputs present. Excluded historical
report directories do not make this a full repository backup.

`python scripts/emergent_deployment_adapter.py build` succeeded with its existing
Intent, Node22.23.2/Yarn1.22.22/Vite8.2.1, two clean matching A/B builds and
isolated frontend/backend package checks. Source and Intent remained clean.
No application worker/startup or operational release lease was run.

- Intent source A: `95b6a51b1d050f448489316a7fd4ffdaf2931dfd`.
- Runtime ID: `rg5-5a396c5f9223d3c4f74f4521a3c13c2ef6b1594de60748569992fb1f79cf4486`.
- Artifact tree: `8da8385888305fb5b97fcfdb27d0ea06820b17d090cd3fe2670b1ddd5e5a66df`.
- build-meta: `9b6c869c3593563a957f218676d16688d2d806de0ee25423364353274369b691`.
- Evidence ZIP: 3,263 bytes, retention two days. Job ~90 seconds.

This result does not approve the modified candidate under the old Intent.
Any later governed candidate acceptance needs a separately authorized intent
freeze/review. None is performed here.

## Dependencies and failure reproduction

Static AST audit covered 736 non-test Python sources. Dynamic loads are literal
standard-library/repository imports plus a controlled supplier module sibling
loader. No application distribution entry-point discovery was found. This is
corroborated by actual server imports in the clean Linux package without
emergentintegrations; it does not establish what proprietary SDK hooks do inside
Emergent's own environment.

The original `backend/requirements.txt` is unchanged. Portable requirements
exclude only emergentintegrations0.1.2; preserve existing pins; add
PyMuPDF1.28.2/qrcode8.2/fonttools4.64.0. fonttools preserves glyph coverage checks.
The first successful resolver result also identified transitive cbor2 6.1.4 and
pyOpenSSL26.4.0, now pinned. ReportLab remains 4.5.1, pillow12.3.0 and
python-bidi0.6.10: no substitution with EXIT-1's side-environment versions.
Bundled Cairo/Noto Arabic fonts and QR asset remain present; fonts-dejavu-core
is declared because generic exports use system font paths.

[Initial runtime run 33998191099](https://github.com/AMASI-SA/AMASI-SA/actions/runs/33998191099)
passed clean no-cache install and `pip check`, real server import and the
loopback-only/negative configuration checks, then correctly failed the
import-write boundary. It is not a passing runtime result.

[Diagnostic run 33998366327](https://github.com/AMASI-SA/AMASI-SA/actions/runs/33998366327)
confirmed `createIndexes` on `mezan_exit2a.bank_transfer_reviews` during import;
no fixture collection content changed. The guard was retained and only
operation names were logged. The narrow source fix and a normal-mode
compatibility test were then added. Final acceptance subsequently passed; see the final results below.

## Isolation contract and limits

The executable role is `web` through the rehearsal entrypoint. `worker` and
`migration` refuse execution. This is deliberate separation for rehearsal,
not an implemented Production worker/migration rollout.

Before importing server, the entrypoint rejects unknown environment names,
any non-exact synthetic Mongo/DB/JWT configuration, all packaged `.env*` files,
and any Linux interface besides loopback. The harness independently enforces
Docker network=none on fresh Mongo with tmpfs data, shares that exact namespace
with the application, drops app capabilities, uses a non-root user/read-only
filesystem and publishes no port. Runtime has no external interface, including
IPv6. Its probe uses documentation-only IP space, not provider endpoints.

Only the test entrypoint substitutes the ASGI lifecycle. It invokes no router
startup/shutdown callbacks, global migrations/indexes/cleanup/seed_admin,
release startup coordination, Qoyod workers, token maintenance, advertising
schedulers or product-taxonomy recovery task. The regular server entrypoint
is unchanged. HTTP routes are restricted to the tested health/Auth/order reads;
all other HTTP routes and WebSockets are denied. Lifecycle performs one bounded
Mongo ping, then exposes phase `rehearsal_ready_no_initialization`; the shared
worker-readiness event stays clear. Health cannot claim verified release identity.

Synthetic Auth fixture/setup writes are permitted and separated from profiled
import/startup windows. This is not full Production authentication/security
acceptance: startup-installed security middleware/indexes are intentionally not
installed, and #1003's continuous Mongo readiness behavior is not incorporated.

## Control-plane ownership evidence

| Item | Status | Evidence / remaining gap |
|---|---|---|
| Emergent project identity/domain mapping | verified | Home Published; View Info salla-analytics / job ab0374e5-2a04-4e34-b24c-447b0238a858 / salla-analytics.emergent.host / mezansalla.com |
| Current resource card | verified UI metadata | Deployment shows Launch, 0.5vCPU/2GB; not measured utilization |
| Workspace machine | verified UI metadata | View Info Large; not the same evidence as Production resource card |
| Live DB mapping in platform panel | partial | Deployment identifies its live-app Database section; does not prove independent ownership/access or Preview separation end-to-end |
| DB provider account owner/roles/export/restore authority | unknown | No provider account page inspected; no connection attempted |
| Backup existence/retention/source/restore test | unknown / blocked | Dedicated DB upgrade advertises an additional backup; not proof of a current backup or restore |
| Persistent files/volumes/object storage ownership | unknown | No storage control-plane evidence |
| DNS/registrar account control | unknown | Custom-domain association is not ownership proof |
| Live code SHA and artifact | unknown | No live probe or inference from latest GitHub export branch |

The Deployment panel unexpectedly rendered an unmasked connection value in a
browser tool result. It was not saved into repository/report/clipboard or used
for access. No value is reproduced here. Later UI reads were restricted to
non-secret headings/controls. No Show/Copy value, database viewer, Logs,
Environment editor, Publish or Re-publish was invoked.

## CI budget

The owner's limit is 45 aggregate standard Ubuntu runner minutes across all
jobs and attempts, no paid overage or automatic retries. Read-only Billing
Overview/Actions for AMASI-SA showed 0/2,000 included minutes and 0/0.5GB storage,
$0 billable after discounts; rechecked before candidate runs. Account usage UI
can round/lag; use measured job duration for this task's own cap.

| Run | Outcome | Approximate seconds | Conservative minutes |
|---|---|---:|---:|
| 33997731256 | Baseline passed | 90 | 2 |
| 33998191099 | Import-write assertion failed | 90 | 2 |
| 33998366327 | Import index write reproduced | 83 | 2 |
| 33998601355 | Import fixed; Auth test expectation failed | 84 | 2 |
| 33998804095 | Full scoped rehearsal passed | 101 | 2 |
| Total | Five manually initiated jobs; no automatic retry | 448 | 10 |

About 7m28s, conservatively 10 of 45 authorized minutes. No runtime artifact
upload; only the baseline evidence ZIP. These are CI measurements, not measured
Production capacity. The final documentation commit uses `[skip ci]` to avoid
unbudgeted push/PR workflows. No pull_request_target trigger exists in the
checked workflows. Required PR checks remain pending, not accepted; this does
not waive Release Guard. [GitHub behavior](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/skip-workflow-runs).

## Final Linux rehearsal — verified within the stated boundary

[Passing run 33998804095](https://github.com/AMASI-SA/AMASI-SA/actions/runs/33998804095),
job 101393879905, tested code SHA `d379c62a7432873e310edd8459da1cae05f17d0c`.
Final delivery adds documentation only and leaves this tested code unchanged.

- No-cache official PyPI install and pip check: passed; original pins retained.
- Two boundary tests: passed, including normal-mode index compatibility.
- Actual server import passed without emergentintegrations or fake modules.
- Mongo profiler/collection snapshots: zero application import/startup writes;
  no new asyncio tasks; worker readiness event stays clear.
- Fixture has enabled=true, auto_send=true, dry_run_mode=false. No worker starts.
  This is not a fully armed Plan B sending fixture or provider acceptance test.
- Docker loopback-only namespace and failed documentation-IP probe: passed.
- Health/readiness, protected routes and synthetic order reads: passed.
  Password login retains mandatory OTP denial (401). A real helper-signed
  synthetic MFA-verified session passes auth/me, one order and logout.
  OTP delivery and full Production authentication middleware are not tested.
- Seven existing preparation PDF tests passed: Arabic/media/QR/layout and
  card/file numbering. DejaVu selection and bundled Cairo Arabic cmap checked.
  Automated PDF assertions are not exhaustive visual review of all templates.
- Worker/migration roles refuse execution. Real Uvicorn stops through TERM with
  exit 0 twice; both restart cycles have zero lifecycle Mongo writes. Harness
  cleans its own temporary containers and tmpfs data.

[Intermediate run 33998601355](https://github.com/AMASI-SA/AMASI-SA/actions/runs/33998601355)
passed the import-write fix but failed an incorrect test expectation that a
password-only token could pass mandatory OTP. Only the fixture was corrected;
application OTP policy was not weakened.

The Python base is digest-pinned (Dockerfile). Mongo7.0.16 resolved to
`sha256:c630c59342c1493d50345136df2af14a76b9e827dd5316bfabee07a0880a5f3a`.
fonts-dejavu-core resolved to 2.37-6. OS packages and the Mongo tag do not yet
constitute a fully hermetic deployment graph; further hardening is deferred.

## Exact files and overlap

Modified existing app file: `backend/bank_transfer_review_routes.py` only.
Added files:

- `.github/workflows/exit2a-baseline.yml`
- `.github/workflows/exit2a-runtime.yml`
- `packaging/exit2a/Dockerfile`
- `packaging/exit2a/Dockerfile.dockerignore`
- `packaging/exit2a/requirements.txt`
- `packaging/exit2a/entrypoint.py`
- `packaging/exit2a/rehearsal.py`
- `packaging/exit2a/acceptance.py`
- `packaging/exit2a/test_boundary.py`
- `packaging/exit2a/audit_source.py`
- `packaging/exit2a/run_linux.sh`
- `packaging/exit2a/README.md`
- `packaging/exit2a/RESULTS.md`

Overlap review: #1003 at d68e5fb20837ae935aa7755df436c26368e784a3 changes server
Mongo/readiness/auth and Qoyod polling. #1013 at
6aecb923f617979106b856fe6e55e718d5c52bd8 changes server middleware/index startup.
No file overlap, branch changes or incorporation. Future integration must
retest shared startup/auth behavior rather than assume compatibility.

## Decision boundary and next required evidence

EXIT-1 install/import/PDF and isolated Linux build blockers are resolved for
this rehearsal scope. The package remains **not deploy-ready**.

1. Review this Draft and choose candidate source/required stability fixes.
   The old Intent authorizes its own A/B relationship, not this changed app
   tree. A separately scoped authorization is needed to freeze/review a new
   candidate Intent and run its governed build. No lease/deploy is implied.
2. Obtain non-secret provider/account ownership metadata identifying the live
   DB versus Preview, owner and backup/restore authority. The next page/document
   should contain only this metadata, not a database viewer or connection values.
   Do not assume a separately owned Atlas account.
3. Obtain backup source/time/retention and storage/volume/object metadata, and
   registrar/DNS control for mezansalla.com. These remain access blockers.
4. Only with separate authorization, test restore and reconciliation of orders,
   preparation history/costs/files and accounting journal, then approve event
   capture/single-writer/rollback from EXIT-1. None starts here.

No hosting purchase, actual restore, Production access, release operation or
platform cancellation occurred. Stop after delivery for the owner's decision.
