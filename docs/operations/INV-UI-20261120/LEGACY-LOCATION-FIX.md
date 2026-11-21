# Legacy warehouse entry compatibility

The active warehouse created before inventory_scope existed was hidden from independent stock entry. The authenticated user response confirmed the field was absent; no customer identifiers, names or address are retained here. Existing bins must keep their identity.

The compatibility rule now accepts an active, undeleted warehouse when inventory_scope is absent or exactly standalone. Explicit shared, null, empty and unknown values remain excluded. Context, preview and the atomic location validator use one shared helper. No warehouse metadata is migrated or rewritten; no stock/accounting/fulfillment flags or permissions are changed.

The source boundary is intentionally expanded only for unclassified legacy warehouse metadata. This supersedes the earlier test requiring every missing scope to be rejected. The former negative scope test is retained for explicit shared scope; new tests cover missing scope as the supported compatibility case and malformed explicit scopes as denied.

All existing per-location checks remain: foreign/mixed or inconsistent occupancy is rejected, positive quantities/capacity/options remain validated, costs stay pending financial valuation, owner-only independent admission remains default closed, stable request IDs prevent duplicates, and transaction rollback remains intact. Existing warehouse documents, bin identities and scan codes are unchanged.

Evidence: the new native regression failed before the fix because context returned no locations. After the fix the complete independent entry suite passed33cases in19.26s on the owned local replica using UUID test databases. The positive regression exercises actual HTTP context/preview/save/replay, verifies warehouse metadata/control preservation and zero fulfillment/accounting effect. Shared/invalid scope, inactive/deleted warehouses, mixed occupancy, stale preview, concurrency/capacity and rollback are covered. No production data is used.

Broader opening-inventory regressions and CI remain pending at this checkpoint. This branch is a development fix, not a governed release or permission to migrate live metadata. No /app operation, lease, merge, publish or real stock write has been performed. Production base d69f31da14f3dc1baad25ab0884ba810446b19f9 is preserved, as are the unrelated shipping/bank changes it contains. The original frozen inventory A/B pair is untouched. A new intent must be built if this changed source is later released.
