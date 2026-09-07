# EXIT-2D preparation with simulated HTTP provider

Local harness only. No business/auth/worker/runtime-guard changes. This is NOT
accepted Linux/application lifecycle evidence and must not be deployed.

## Blocking runtime contract

`independent_runtime.validate_before_import` requires APP_ENV=test, the exact
synthetic configuration and Linux loopback only. It rejects every environment
variable containing TOKEN_ENC_KEY. The real Salla crypto client requires
SALLA_TOKEN_ENC_KEY to decrypt its stored access token. Therefore this harness's
new preflight exits 1 with BLOCKED_SYNTHETIC_PROVIDER_KEY_GUARD, before Docker
builds, credentials, database or web startup. It also blocks unknown future
runtime contracts pending review. Do not rename the key, inject it after
validation, use APP_ENV=production, monkeypatch crypto or weaken the guard.
There is no authorization in this task to change that runtime contract.

The existing exact DB_NAME is also enforced. Prepared scenarios use separate,
newly created network-none Mongo containers, each with its own tmpfs database
named mezan_exit2c. They never share database contents or change flags mid-cycle.

## Prepared scenarios (not executed against the application)

`run_preparation_linux.sh` prepares deny, unavailable, success sequentially.
Each scenario has fresh synthetic access token and encryption key, never printed
or persisted as a key file. The existing owner/employee credentials remain the
public synthetic acceptance fixtures. No refresh token is needed: the access
token expires one hour after seeding. Unexpected OAuth requests are rejected.

The denied fixtures start experiment=true / salla_status_writes_allowed=false;
the success fixture starts experiment=false / writes=true. Only the local
simulator is reachable. Seed inserts raw orders/users/products/images/supplier
and the initial integration/flags; no batch, allocation, registry or piece.

- Deny: discovery succeeds, status POST returns403. Review must return502,
  remain pending_review, and create no preparation entities.
- Unavailable: status discovery returns503. Same application rejection required.
- Success: real application review/status discovery/POST and verification GETs
  receive narrowly declared simulated replies. Then real creation/allocation,
  supplier/receipt/assembly APIs, two web restarts and PDF/image/history checks.
  The original owner/employee sessions stay inside one controller's memory.
- Shipping: the real carrier-label service's GET /orders?keyword=... lookup
  returns503. This is an attempted/failed label path, not a POST shipment test.
  The final assembly response must report ready=false; persisted workflow must
  remain completed with assembly_status=completed and carrier_label_status=failed.
  No AWB/tracking ID is fabricated. Its restart snapshot is compared afterward.

API/Auth bases are exact numeric loopback URLs, proxy variables are refused,
the server never returns Location or forwards a request, and Docker publishes
no ports. The runtime shares a verified network=none Mongo namespace. Unknown
method/path/body/order/token/transition fails; no generic200 response exists.
Request bodies/headers/tokens are not logged. Prepared runtime evidence exposes
only integer counters. live_provider_calls=0 is bounded by namespace isolation;
simulated_provider_calls counts actual requests, not successful transitions.
Local simulator tests have their own HTTP count, separate from unrun application
counts. A simulated sent never proves a real Salla synchronization or an
experiment mode that works without provider confirmation.

## Revision reachability, still unaccepted

Source: reviewed_preparation_v3.py stable_ready_item_id / stable_ready_unit_id /
stable_reviewed_line_revision / stable_reviewed_product_revision;
reviewed_products_catalog.py expand_reviewed_ready_units and
order_items_with_review_snapshot; order_review_routes.py patch_review_item;
reviewed_preparation_batches.py selection validation.

Identity contains order_number, order_item_id, line_index, unit_index. Line
revision hashes product/source/variant IDs, SKU/barcode, quantity and options.
Product revision includes line revision and available indices/remaining counts.
Physical cards expose one index and remaining=1. Display name/image changes do
not change the revision. Reserving another physical card does not prove a change
to this card; reserving this card removes its availability.

The reviewed-item PATCH rejects completed review. Operational-item rename does
not provide a quantity/options mutation for this physical merchandise line.
The specified preparation path exposes no established legal transition from
R1 to R2 for the same still-available unit. Live-source updates may affect live
lines, but a resync/backfill is outside this task and is not a proven transition.
This is a finding about the reviewed paths, not a universal theorem about every
endpoint or all legacy/recovery records.

Local primitive tests establish display invariance and that a hypothetical
quantity change alters revision without altering unit ID. They do NOT establish
an HTTP transition. The lifecycle keeps exact old-selector reallocation rejection
but reports its actual reason separately; it never marks stale-revision accepted.
Full lifecycle PASS is explicitly qualified by this remaining revision gate.

## Remote plan, NOT authorized

Local workflow: .github/workflows/exit2d-preparation-lifecycle.yml. Exact new-branch
push, one ubuntu-24.04 job, contents:read, persist-credentials:false, dedicated
non-cancelling concurrency, no secrets/reusable workflow/artifact/deploy steps.
Records checkout HEAD and verifies github.sha. Runs local contracts followed by
`bash packaging/exit2c/run_preparation_linux.sh`. Current source will fail its
preflight. Do not spend CI on that known blocker.

After a separately approved runtime-contract decision and a reviewed new SHA:
estimate12-20 aggregate minutes cold for the three sequential isolated scenarios
(images currently rebuild per scenario), proposed job timeout25 minutes, with
separate budget/cleanup allowance approval before launch. This is an estimate,
not a benchmark or CI authorization. No retries. Recheck standard-runner/public
status, existing zero-paid-storage stop control and concurrent jobs first.

Current exact-branch push without PR matches only the new workflow. Existing
candidate requires its original branch and is not rerun. The 13 current default
main workflows were individually read: no completion event chain/reusable job
call was found. Re-enumerate on fresh refs before any later push.

An eventual PR to hotfix/prod-snap-meta-final includes the whole inherited diff,
not just this harness. Current filters match15 PR workflows: auth-email-otp,
auth-passkey, auth-progressive-login, CodeQL(two languages), Employees V2, MCP,
Release Readiness, Mobile Session, Campaign AI Worker, Preparation Piece Ops,
Qoyod Memory Bounds, Qoyod Payment Freshness, Runtime Stability, Security Gate,
Snapchat Settings. The new workflow has no PR event. Opening a PR after a branch
push adds those15; later pushes to an open PR add15 plus the new push workflow.
CodeQL's processed SARIF check is additional to runner jobs. This is a source
filter projection, not a guarantee about future refs/conditional jobs.

Release Readiness has shared mezan-release-readiness concurrency; Security Gate
and Snapchat use ref/PR-scoped cancelling groups. Do not cancel another task.
Release Readiness's existing frontend intent contract is still failed; its
conditional Node20/manual redeploy steps and Snapchat scope-skipped tests retain
their actual states. No old-contract Readiness retry or new Intent is authorized.
A PR requires separate full-event/release-contract approval, not a base chosen
to hide required checks. Prior PR round38 minutes cannot be reused as a budget;
a complete later combined round needs a fresh job-by-job estimate and cap.

Production backup/files/decryption settings, real supervisor and final governed
release package remain independent gates. CI ledger unchanged:38/40;112 cumulative.
