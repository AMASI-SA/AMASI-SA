# Pending shipping print: verified option difference (2026-10-20)

Order: 294529484. Read-only production observations; no customer data retained.

PR1452 was deployed and verified (source bc85eddf3d5c92b370c2f59d17ac79946763863e,
deployment 571e1704fc823c681c61b2f31de1db4f5ea5f55a). The original raw/watermark
clock gap was corrected, but real normal-UI printing remains blocked.

Current source: completed; snapshot revision 11, pending true; provider update
2026-10-19T19:25:27Z. Accepted lifecycle: revision 6, generation 4,
legacy_operational_pre_g47, accepted true, retry false; source update
2026-10-19T19:25:17Z. No consumption plan exists. Payment is paid with zero
remaining. Ready/completion was not changed.

The completed review operation retains option value name `بلوفر + ترنج`.
The current Salla raw order uses `سويتر + ترنج`. Option ID 1844029573 and
selected value ID 1706595788 are unchanged. Product 902697380, order item
1616038569 and quantity 1 are unchanged in the compared component contract.

A pure source-based fingerprint probe with canonical payment fallback gives:
- Current value: 0e961c920edce8913e640c09ceacda2d500f24bee2990ec9bd1098dc2265c273
- Approved value: b68e928d08dd44445c3d35e936b62ecc129e062813e73a8909bea150995c7832
The approved result exactly equals the persisted lifecycle fingerprint.
Changing only the option label reproduces the mismatch. Derived nested
option_value and null product_sku_id do not explain it.

Exact refusal: revalidate_unchanged_component_snapshot compares
component_order_fingerprint(order) with previous.source_fingerprint and
returns component_reconciliation_required on inequality. Pending remains true;
the print status guard then returns the generic frozen-print message.

Do not interpret stable selected-value ID as proof of identical physical goods.
Owner clarification is pending: catalogue label-only rename vs changed requested
product. Do not automatically accept changed requirements, clear pending, delete
components/services, alter Ready, reserve/release stock, or publish a bypass.
No source-code fix on continuation branch yet. Local probe lives outside repo.

## Owner decision and implementation checkpoint

Owner confirmed the observed change was a label-only rename of the same goods,
and explicitly requested that catalogue renames across multiple products do not
block printing. Latest scope: printing first; receiving/Ready behavior unchanged.

WIP continuation: opt-in print-only legacy/no-plan recovery. Current immutable
completed review must be owner/order bound, integrity checked and unsuperseded.
Only fixed radio/select choices with unchanged option/value IDs and all option
siblings/prices may restore approved labels. Reconstructed entire component
fingerprint must match acceptance; subsequent renames use integrity-bound audit
lineage. Unknown, quantity, actual choice, cancellation and inventory-plan changes
stay blocked. No component, stock, workflow or approval mutations.

Five local pure regression tests pass. Windows Application Control prevents
loading the existing PDF/PIL native dependency for full local integration, so real
Mongo transaction/print tests run in the existing CI replica-set workflow. Not yet
published; real UI print acceptance remains pending. Follow protocol v5 only.

## Requested notification addition

Owner requested red bell on changed assembly product options in web and Android,
showing previous/current field names and values. New orders initially approved
with new values must have no bell. Added a read-only projection from each order's
own current integrity-verified review, exact option identity matching, with no
acceptance or Ready side effects. Web interaction test is included in CI.
Android UI is developed separately on its existing mobile worktree.

CI first run exercised recovery successfully including repeated renames; one
assertion incorrectly prohibited the existing refresh's carrier-proof invalidation
fields. It now explicitly checks unchanged original workflow/Ready fields and
warehouse stock, permitting only those five existing carrier fields. Source is
not yet approved for release; rerun all required checks at the updated source.
