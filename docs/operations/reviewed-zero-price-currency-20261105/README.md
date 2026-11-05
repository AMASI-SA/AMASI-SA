# Reviewed items blocked after a source refresh

The assignment policy already permits remaining locally reviewed units while
an order is internally in progress and Salla remains under review. The observed
failure is a pending component source snapshot: provider options can switch the
currency label of a zero amount between order and store currency. The raw option
is part of the component fingerprint, so the previous exact-match check loses
acceptance and the whole order disappears from the remaining-unit catalogue.

The fix proves the narrow change against the completed immutable review snapshot.
It restores only zero-price currency labels in a temporary raw copy, remaps the
items, and requires the complete historical component fingerprint to match. It
preserves all existing source-version, cancellation, owner transaction, inventory
generation and plan guards. Unknown option metadata and actual business changes
remain blocking. Repeated currency changes retain the same approval lineage.

No raw provider facts, review operation, external status, stock, preparation
allocations or custody are rewritten. Future ingestion uses the existing fenced
revalidation seam. Existing pending snapshots require explicit recovery.

## Read-only diagnosis after deployment

```bash
python scripts/audit_pending_reviewed_products.py --user-id <merchant>
```

The report separates all pending active approved orders from exact currency-only
matches and their remaining supplier-file units. Quantities use the allocation
ledger, not the cached preparation progress. Errors are explicit and cause a
nonzero exit. This report is advisory; live data can change after it runs.

For each reviewed eligible order, preview and then apply its exact revision:

```bash
python scripts/revalidate_component_snapshot.py --user-id <merchant> --order-number <order> --expected-revision <revision>
python scripts/revalidate_component_snapshot.py --user-id <merchant> --order-number <order> --expected-revision <revision> --apply
```

Application rereads all evidence under the owner transaction. A concurrent source
change refuses the stale revision. Check the active catalogue and downstream
assignment after recovery. Never clear the pending flag manually.

Integration tests use an isolated Mongo replica set and the real source refresh,
revalidation, catalogue, allocation ledger and read-only audit. They assert Salla
status stays under review, previously allocated units do not reappear, local
workflow stays unchanged, and no stock reservation occurs during refresh.
