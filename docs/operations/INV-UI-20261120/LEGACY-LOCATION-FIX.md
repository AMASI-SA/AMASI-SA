# Legacy warehouse entry compatibility

The active warehouse created before inventory_scope existed was hidden from independent stock entry. The authenticated user response confirmed the field was absent; no customer identifiers, names or address are retained here. Existing bins must keep their identity.

The compatibility rule now accepts an active, undeleted warehouse when inventory_scope is absent or exactly standalone. Explicit shared, null, empty and unknown values remain excluded. Context, preview and the atomic location validator use one shared helper. No warehouse metadata is migrated or rewritten; no stock/accounting/fulfillment flags or permissions are changed.

The source boundary is intentionally expanded only for unclassified legacy warehouse metadata. This supersedes the earlier test requiring every missing scope to be rejected. The former negative scope test is retained for explicit shared scope; new tests cover missing scope as the supported compatibility case and malformed explicit scopes as denied.

All existing per-location checks remain: foreign/mixed or inconsistent occupancy is rejected, positive quantities/capacity/options remain validated, costs stay pending financial valuation, owner-only independent admission remains default closed, stable request IDs prevent duplicates, and transaction rollback remains intact. Existing warehouse documents, bin identities and scan codes are unchanged.

Evidence: the new native regression failed before the fix because context returned no locations. After the fix the complete independent entry suite passed33cases in19.26s on the owned local replica using UUID test databases. The positive regression exercises actual HTTP context/preview/save/replay, verifies warehouse metadata/control preservation and zero fulfillment/accounting effect. Shared/invalid scope, inactive/deleted warehouses, mixed occupancy, stale preview, concurrency/capacity and rollback are covered. No production data is used.

Broader opening-inventory regressions and CI remain pending at this checkpoint. This branch is a development fix, not a governed release or permission to migrate live metadata. No /app operation, lease, merge, publish or real stock write has been performed. Production base d69f31da14f3dc1baad25ab0884ba810446b19f9 is preserved, as are the unrelated shipping/bank changes it contains. The original frozen inventory A/B pair is untouched. A new intent must be built if this changed source is later released.

## Reviewed source/intent milestone

Source A `56af6ab9d997688311f339eab8d2ca12f6a0e034` and intent-only B `71c53711796e972b9cdca4b1c7258709807be05e` are pushed. Source CI completed21successful jobs,6conditional skips and0failures. Broader local native tests94PASS and frontend7PASS. Candidate run44919705717 artifact14713103126 was independently matched against2948Git source records, including the new shared helper. Raw generated intent SHA256 `276b7ff2a54d39468b0a7fedbb32ab754ae7e053a4febdd180956d4c722a3625` was tracked without byte changes. Runtime identity `rg5-f6614ad3eee05b048bc4b3b606d6a8fdb602438f8ee0db19ef61273b91e1eaac`. B changes only the intent file.

Exact BCI and Node20/24 reviewed clean-clone checks remain pending. No merge/deployment or production metadata/quantity writes. Post-freeze canonical status/evidence is maintained on the separate original continuity branch so A/B stay immutable.
