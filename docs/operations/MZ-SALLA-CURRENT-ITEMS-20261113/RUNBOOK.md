# Current Salla item projection

## Problem and prevention

The legacy products merge kept the longer array. An accepted provider update
could therefore leave one native item in `raw_by_source.salla_direct.items`
while retaining three items in root `products`. Order Engine read the current
native item; current-cost reports counted the two obsolete projected items and
could not resolve their options.

The fenced writer now builds `products` from the same merged native snapshot.
Native item IDs, rather than SKU or names, determine membership and the existing
raw option-preservation logic. The legacy mapped option representation remains
unchanged because adding native option fields to it would change historical
cost fingerprints for otherwise identical lines.

The existing Salla item readers share validation and complete pagination.
An internal `CompleteOrderItems` result is required to accept an empty final
list. A plain empty array in a light event cannot erase a legacy order's known
products. Missing native identity retains the compatibility fallback. This
patch does not redefine whether a webhook is a complete provider snapshot;
it keeps the products projection consistent with the source already accepted
by the existing G47 version and cancellation guards.

There is no new scheduler, polling interval, automatic batch repair or cache
worker. Normal existing accepted updates repair the projection as they arrive.

## Explicit repair of existing drift

Use the CLI only for individually reviewed order numbers belonging to one owner.
The limit is 25 orders and 5,000 projected lines per batch. It is not a discovery
or backfill command. Configure connection values securely in the operator's
existing environment; never paste them into a log, issue or PR:

- `MZ2_ITEMS_PROJECTION_REPAIR_MONGO_URI`
- `MZ2_ITEMS_PROJECTION_REPAIR_DB_NAME`

Preview using synthetic placeholders below, substituted only in the authorized
operator session:

```bash
python scripts/repair_salla_products_projection.py \
  --user-id synthetic-owner --orders synthetic-order
```

The preview makes bounded GET requests for details, complete items and details
again. Native order/reference IDs, source version, status and full item evidence
must agree with the accepted stored source. Credential refresh or token writes
are forbidden; use a working authorized connection. A refusal requires resolving
the stated source discrepancy, not bypassing the checks.

Review the before/after IDs and quantities, removed IDs, reasons and
`manifest_sha`. The output omits customer details and customization text. Apply
only an approved, fresh, ready plan:

```bash
python scripts/repair_salla_products_projection.py \
  --user-id synthetic-owner --orders synthetic-order \
  --apply --manifest-sha REVIEWED_64_CHARACTER_SHA256
```

Apply repeats the source proof and manifest, rereads inside the existing owner
transaction and uses compare-and-set predicates. A changed order, source,
version or projection invalidates the plan. A failed comparison rolls back the
whole batch. Only `products` and `field_sources.products` may be updated.
Raw snapshots, order totals, source clocks, G47 revision, preparation, inventory,
cost snapshots, journals and invoice state are outside the write set.

Preview again after success; the result must be `no_op`. Then reload the order
and its report with the same date/status filters and compare native line IDs,
quantity and current cost. If saved financial or preparation facts differ from
the current order, retain their existing discrepancy workflow; this repair does
not rewrite historical evidence.

## Verification and release

The G47 workflow runs unit and real Mongo replica-set acceptance on the exact PR
head. It rejects skipped, failed or uncollected projection transaction tests and
retains the JUnit evidence. The regression covers a synthetic 13-piece incomplete
report becoming 11 complete pieces, without setting any missing cost to zero.

Follow repository `AGENTS.md` for release protocol v5. Source/CI completion is not
a deployment or a live data repair. After separately authorized deployment,
verify the governed production identity before previewing the bounded repair.
Record the exact source, deployment and approved repair evidence in the task
status and Issue #1006 without customer data or credentials.
