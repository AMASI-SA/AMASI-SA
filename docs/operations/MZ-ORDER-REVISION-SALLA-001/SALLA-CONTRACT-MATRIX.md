# MZ-ORDER-REVISION-SALLA-001 — Salla Contract Matrix (P0)

## Status

`BLOCKED_PENDING_SALLA_SANDBOX_ACCESS`

- Current integration baseline: `9f8b16998f62cbdb08dc9361bc2e443223d27d17`
- Recovered research source: `684d9c9a8120d2158a02bb955500a415474b1279`
- Working branch: `fix/order-revision-p0-webhooks-20260916`
- Original research branch: `research/mz-order-revision-salla-contracts-p0`
- Review date: 2026-09-16 (UTC)
- Production writes performed: **none**
- Product code changed: **none**

## Recovery and webhook evidence

The recovered source is a research CLI and its local contracts, not a running
order editor. It does not register an application route, change the order page,
or enable Salla writes in Production. P0 completion still requires the real
Sandbox matrix below; local test success cannot complete that matrix.

The earlier CLI read the webhook file before the mutation and reused that
snapshot afterward. A reproduced case delivered one event after the write but
recorded zero events while returning a passing operation result.

The repaired CLI collects fresh events after the mutation and readback for a
bounded observation window. `--webhook-wait-seconds` defaults to five seconds,
accepts zero through 30, and applies only when `--webhook-events` is supplied.
Use a dedicated JSON event array for the reviewed case, updated atomically by
the Sandbox webhook exporter. Events must retain the same store, order and
correlation identity. Existing, stale, future and repeated observations do not
substitute for a new correlated event.

`observed_verdict` describes the item-operation postconditions. The final
`verdict` remains `INCONCLUSIVE` when those postconditions pass but the webhook
source is missing or no qualifying event arrives. A malformed or unreadable
source after the write is recorded as `UNKNOWN` and never triggers another
commercial write. CLI exit zero means evidence was written; it does not by
itself mean the Sandbox case passed, payment behavior is proven, or P1 may
start. Payment, inventory, option and state evidence below remain separate
requirements.

The dedicated `Salla Order Revision P0 contracts` workflow runs only isolated
test doubles and full schema validation. It receives no live store credentials
and does not invoke a Sandbox write command. Current continuation status is in
`STATUS.json` beside this document.

This document distinguishes documented API capability from behavior proven in
a Salla Demo Store. An endpoint being documented is not evidence that Salla
accepts it for every order or payment status.

## Environment gate

No verified Demo identity, seeded Demo order identifiers, or credential
resolver environment was available in the execution environment. The runner
does not accept or persist a raw Salla access token. It resolves the token from
the existing encrypted integration through
`salla_integration.service.ensure_fresh_access_token`. The following variable
names are required; no secret value is printed:

- `SALLA_SANDBOX_BASE_URL` (must be `https://api.salla.dev/admin/v2`)
- `SALLA_DEMO_STORE_ID`
- `SALLA_DEMO_TOKEN_SCOPES` (`orders.read_write` plus product read access)
- `SALLA_SANDBOX_SEED_MANIFEST`
- `SALLA_SANDBOX_EVIDENCE_DIR`
- `MONGO_URL`
- `DB_NAME`
- `SALLA_DEMO_STORE_CONFIRMED=true`
- `SALLA_SANDBOX_RUN_WRITES` (must equal `true` for writes)
- `SALLA_P0_WRITE_APPROVAL_ID` (unique to one reviewed attempt)
- `SALLA_P0_WRITE_APPROVAL_ISSUED_AT` (timezone-aware ISO-8601; five-minute
  approval lifetime)

The resolver selects exactly one `salla_integrations` owner by the configured
Demo Store id; a missing or ambiguous match fails closed. Missing `MONGO_URL`
or `DB_NAME` stops a run before an HTTP transport or POST exists and emits
`ADD_EXECUTED=false` with
`REASON=P0_CREDENTIAL_RESOLVER_ENV_UNAVAILABLE`.

No imported API can construct or receive a write-capable HTTP transport.
`run_case` is a compatibility/prevalidation gate and always stops with
`SALLA_P0_LIVE_EXECUTOR_REQUIRED`; `analyze_attempt` is pure analysis and always
returns `MOCK_CONTRACT_FIXTURE / NOT_EXECUTED`. A caller-controlled transport
attribute or successful identity response cannot promote Mock output.

Only the CLI invocation owns the credential and HTTP closure. For a write it
first validates the complete case/webhook input and one-time approval, resolves
the encrypted credential, proves `/store/info` Demo identity, completes the
readiness sweep, and obtains a successful fresh baseline. It then creates a
capability with at most a 60-second lifetime, bound to the attempt id,
one-time approval digest, request correlation id, and canonical Seed, case,
and operation digests. Immediately before **each** non-GET attempt, including
an authorized retry, the sealed closure rechecks the expiry, every binding,
and remaining write budget and consumes one budget unit. That capability is
not returned, registered, or reusable. There is no public HTTP constructor
that accepts a raw token.

All required runtime values were absent. Consequently, the state matrix below
is intentionally marked `NOT_EXECUTED`; filling it with inferred pass/fail
results would be false evidence. P1 must not start until this matrix has real
Demo Store evidence or an official Salla mock environment that models the
status restrictions and payment side effects.

## Documented endpoint contracts

| Operation | Method and path | Scope | Documented request facts | Documented response facts | What remains unproven |
|---|---|---|---|---|---|
| Create item | `POST /admin/v2/orders/items` | `orders.read_write` | `order_id`, identifier, quantity, branch, options, price/cost/weight | Returns order item rows including item `id`, SKU, quantity, amounts and option/value identifiers | Allowed order statuses; paid-order balance; inventory reservation; webhook set; retry semantics |
| Update item | `PUT /admin/v2/orders/items/{item_id}` | `orders.read_write` | `order_id`, quantity, branch, options, price/weight | Returns updated order item representation | Whether options change the resolved variant; whether `item_id` stays stable; paid-order balance |
| Delete item | `DELETE /admin/v2/orders/items/{item_id}` | `orders.read_write` | Path item id | Success/message envelope | Allowed statuses; stock restoration; total/payment/refund behavior; retry semantics |
| Read items | `GET /admin/v2/orders/items?order_id=...` | `orders.read` | Order id | Authoritative item list | Read-after-write convergence time |

Official references reviewed:

- Salla Merchant API, Create Order Item
- Salla Merchant API, Update Order Item
- Salla Merchant API, Delete Order Item
- Salla Merchant API, List Order Items
- Salla order webhook model and changelog (`order.products.updated`,
  `order.total.price.updated`, `order.payment.updated`, `order.updated`)

## Required status matrix

Legend:

- `NOT_EXECUTED`: no Sandbox evidence exists yet.
- `PASS`: request accepted and all recorded postconditions matched.
- `REJECTED_BY_SALLA`: Salla rejected the operation; record sanitized response.
- `AMBIGUOUS`: response succeeded but read-after-write/payment/inventory facts did
  not converge or could not be attributed.

| Order state | Create item | Update item | Delete item | Evidence |
|---|---:|---:|---:|---|
| `pending` | NOT_EXECUTED | NOT_EXECUTED | NOT_EXECUTED | Sandbox order required |
| `under_review` | NOT_EXECUTED | NOT_EXECUTED | NOT_EXECUTED | Sandbox order required |
| `in_progress` | NOT_EXECUTED | NOT_EXECUTED | NOT_EXECUTED | Sandbox order required |
| `paid` | NOT_EXECUTED | NOT_EXECUTED | NOT_EXECUTED | Demo order and Order API payment facts required |
| `partially_paid` | NOT_EXECUTED | NOT_EXECUTED | NOT_EXECUTED | Demo order and Order API payment facts required |
| `completed` | NOT_EXECUTED | NOT_EXECUTED | NOT_EXECUTED | Sandbox order required |
| `cancelled` | NOT_EXECUTED | NOT_EXECUTED | NOT_EXECUTED | Sandbox order required |

## Per-case evidence record

Each executed cell must record, with tokens and customer data redacted:

1. Sandbox store and fixture identity.
2. Order id/reference and status id/slug before the call.
3. Method/path, idempotency/retry attempt number, sanitized request body.
4. HTTP status, sanitized response body and response/request correlation headers.
5. Source item id before and after.
6. Product, variant, SKU, quantity, option ids/value ids, price and order total
   before and after a fresh `GET /orders/items` plus Order Details fetch.
7. Inventory/branch quantity or reservation facts before and after.
8. Webhooks received, their ids, timestamps and ordering.
9. Repeated identical call outcome.
10. Simulated lost-response outcome: do not repeat a commercial write until a
    read-after-write reconciliation proves whether the first call committed.

Before any credential DB or Salla transport I/O, every case is validated
against an allowlist. The top-level and body `order_id` must match the same Seed
order; the item, product, variant, SKU, option/value and branch identifiers must
be relationally bound to one Seed tuple. Flat membership lists are not enough:
each variant id is paired with its own product, SKU, and exact option/value set,
and each value is paired with its option. Unknown fields and identifiers,
non-literal JSON booleans for `retry_once` or
`disposable_order_confirmed`, malformed assertions, and malformed webhook
records fail closed.

A fresh Order Details plus Order Items baseline with `fetch_ok=true` is
mandatory immediately before a write. The runner creates an exclusive,
immutable local `*.intent.json` record before the attempt and a separate
`*.terminal.json` record afterward. Absence of the terminal record means
`UNKNOWN`; this is local-filesystem research evidence only and makes no claim
of durable database audit storage. Before an UNKNOWN-capable lease exists, the
runner atomically reserves the one-time approval; a reused approval is rejected
without creating an orphan lease. An append-only local P0 ledger additionally
holds an exclusive per-order lock, rejects the same operation replay, rejects
concurrent attempts, and quarantines an order after an absent terminal or
`UNKNOWN` outcome.

The ledger is deliberately separate from user-selected evidence and manifest
paths. Its canonical root is
`.git/mz-p0-local-state/MZ-ORDER-REVISION-SALLA-001` under the resolved Git
**common directory**, so every linked worktree of the same clone shares the
same approvals, attempt records, and order locks. Changing the working
directory, an evidence directory, an environment variable, or invoking the
runner through a repository-relative symlink cannot select another root.
Directories must be owner-only `0700`, ledger JSON/lock files must be `0600`,
and Git pointers, state paths, or components involving unsafe symlinks fail
closed. This replay guarantee is **only local to the same repository clone on
the same host while that common Git state is preserved**. A different
clone/host or deletion of the local state is outside the guarantee; P0 does not
claim a global one-use approval or shared durable lock without a trusted
shared state service.

Before any evidence or terminal record is persisted, the resolved token's
exact value is redacted recursively even when it appears in dictionary keys,
below generic keys, or inside list/string values, in addition to key- and
bearer-pattern redaction. A sanitized-key collision fails closed instead of
overwriting or persisting an ambiguous record.

`PASS` is determined by fixed operation postconditions, never by case-file
assertions alone. Create must add exactly one Seed-bound target and preserve all
existing items. Update cases without a mutable requested field are invalid;
the baseline must differ from the requested value for at least one requested
field, the fresh result must equal every requested value, and no unrequested
mutable field or unrelated item may drift. Delete must remove only the target
while preserving the verified replacement. Both before/after fetches and order
identities must match. Case-file assertions are supplemental.

Transactions and Branch APIs are excluded from P0 because those scopes are not
granted. Payment facts are extracted only from Order API, Order Items, and
correlated webhooks. `branch_id` comes from the reviewed Seed/order/product
data. No transaction-level behavior may be marked proven.

## Product configuration cases

The live matrix must repeat relevant cells for:

- Product without options.
- Size and color options.
- Text option.
- Checkbox option.
- Multiple quantity.
- Variant-changing option selection.
- Replacement flow: create replacement first, verify it, then delete the old
  item. `delete-first` is prohibited. A DELETE case must carry the exact
  ordered create/verify/delete proof, bind every step to the same Seed order
  and old item, bind the replacement product/SKU to the replacement Seed
  fixture, and prove the resulting replacement item in the fresh baseline.
  DELETE with missing, reordered, mismatched, or self-replacing proof is
  rejected before the DELETE request.
- Different customization per unit, represented as separate commercial lines
  unless Salla proves a per-unit contract.

## Dynamic option evidence boundary

The documented Order Items representation includes option ids,
`product_option_id`, option type and value ids/names. It does not by itself
prove an official conditional-dependency graph such as “yes makes name
required”. P0 must separately capture the Product Details/Options responses for
the Sandbox products and determine whether Salla exposes stable dependency and
requiredness identifiers. Text labels must not be used as relational keys.

## Difference-payment decision

Current decision: `UNPROVEN_ON_ORIGINAL_ORDER`.

The Order Items endpoints document item and amount mutation but do not document
that adding/updating an item on a paid order will:

- create an outstanding balance on the original order,
- produce a checkout/payment URL, or
- let the customer pay only the difference.

No original-order difference-payment flow may be implemented until the paid
and partially-paid Sandbox rows prove those postconditions. If they do not,
the approved fallback is a Salla supplemental order linked operationally to the
original order. Mezan must not fabricate a financial line, payment status or
refund.

## Exit criteria for P0

P0 is complete only when:

- all 21 state/operation cells have sanitized Sandbox evidence;
- the required product/option cases have evidence;
- item-id stability and variant behavior are decided;
- webhooks and retry/lost-response behavior are recorded;
- positive-difference behavior is proven or supplemental order is selected;
- the document status changes from `BLOCKED_PENDING_SALLA_SANDBOX_ACCESS` to
  `COMPLETE` with evidence references.

Until then, P1 is not authorized to start under the approved phase gate.

## Runner commands

The P0 runner is intentionally Linux/POSIX-only. It requires Linux `flock`,
`O_NOFOLLOW`, ownership, and mode semantics and exits with
`SALLA_P0_LINUX_POSIX_REQUIRED` before importing `fcntl` on an unsupported
host. Do not run this evidence workflow through native Windows or PowerShell.

Full schema verification requires `jsonschema==4.26.0`, already pinned in
`backend/requirements.txt`. Its absence is
`FULL_DRAFT202012_VALIDATION=BLOCKED_MISSING_DEPENDENCY`, a failed verification
preflight; the suite never labels syntax-only or skipped checks as full Draft
2020-12 validation.

From the repository root on Linux, populate the dedicated Sandbox variables
from `.env.salla-sandbox.example`, then run the read-only gate:

```bash
python scripts/research/salla_order_item_contract_runner.py readiness
```

After readiness succeeds and the owner explicitly opts into destructive
Sandbox writes, run one reviewed case at a time:

```bash
export SALLA_SANDBOX_RUN_WRITES='true'
export SALLA_DEMO_STORE_CONFIRMED='true'
export SALLA_P0_WRITE_APPROVAL_ID='unique-approved-attempt-id'
export SALLA_P0_WRITE_APPROVAL_ISSUED_AT="$(date -u +'%Y-%m-%dT%H:%M:%SZ')"
python scripts/research/salla_order_item_contract_runner.py run \
  --case-file docs/operations/MZ-ORDER-REVISION-SALLA-001/fixtures/sandbox-case.example.json \
  --webhook-events /path/to/sanitized-webhook-events.json
```

The runner never accepts `SALLA_ACCESS_TOKEN` or a plaintext Sandbox token. It
uses only the repository's encrypted Salla credential resolver. Missing
Sandbox configuration returns `SALLA_SANDBOX_NOT_CONFIGURED`; missing resolver
environment returns `P0_CREDENTIAL_RESOLVER_ENV_UNAVAILABLE`. Mock transport
output is always `MOCK_CONTRACT_FIXTURE / NOT_EXECUTED`; only the sealed CLI
attempt after resolver, Demo identity, approval, baseline, digest, TTL, and
local-lease checks can emit `SALLA_DEMO_STORE_EVIDENCE`.
