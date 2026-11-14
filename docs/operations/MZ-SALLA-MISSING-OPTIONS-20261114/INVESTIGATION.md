# Missing incoming Salla customer options: read-only investigation

## Scope and current conclusion

The owner requested the reason an existing order appears without customer
choices and with incomplete catalog cost, explicitly without a manual Salla
refresh. This is a separate incident from the stale item-membership correction
in PR #1908 and its unfinished manual repair surface in Draft PR #1909.

The live native Order V2 DTO and root product projection both have empty
customer choices. The first accepted native snapshot remains revision 1; its
source clock still equals the initial persisted arrival. This rules out a
purely visual rendering problem and a root-only report projection explanation.
It does not establish whether the original captured event omitted choices or
used an unhandled shape. No incident/customer/store/owner identifiers or raw
payloads are retained in this record.

The background status GET showed a fresh heartbeat, no process restart, and
`salla-cost-options: true`. This proves task liveness only: its loop catches
exceptions, so it is not evidence of a successful attempt for the target.

**A separate compatibility defect is reproducible:** the documented List Order
Items response may contain selected options and a native line ID but omit the
product ID. The current recovery accepts this array at the complete-reader
boundary, then rejects it because report evidence requires an explicit matching
provider product ID. The actual target's attempt record and List Items response
have not been obtained; attribution of that defect to this incident is pending.

## Source and runtime identity

- Reviewed branch: `hotfix/prod-snap-meta-final`.
- Reviewed merge: `29ec1e07eb7d1a23647cadff5bac26149accfa99`.
- Observed live source: `2cf4d70d7e0c23c11048cad5c610e8c0c60d7eeb`.
- Runtime release:
  `rg5-5c3980b0f83f3859023070ef846486e877849ca7ca848367a181995ca32c1d5e`.
- One read-only health observation reported verified identity, matching critical
  hashes, verified frontend/backend and bound release-control source. This is
  not a new official three-probe release verification by this task.
- A diff between the source and merge changes only the release intent.

## Confirmed source behavior

1. `salla_integration/webhook_order_sync.py` maps accepted webhook payloads
   directly without a provider hydration call at intake.
2. `orders_db.py` preserves previously known choices for the same native line
   and projects root products from the merged native snapshot. It cannot
   manufacture choices missing on first arrival.
3. `order_engine/mapper.py` preserves option dictionaries in `options_raw` and
   considers several common choice fields. Both stored consumers being empty
   points upstream of the screen and cost resolver.
4. `report_option_identity.py` and `order_cost_authority.py` intentionally leave
   a bound choice unresolved when no selected/non-required evidence exists.
   Missing choices must never be replaced with zero cost.
5. `salla_integration/cost_option_recovery.py` invokes the existing List Order
   Items reader and fills only empty choices under its existing source fence
   and owner transaction. It scans bounded new/history lanes and keeps durable
   retry state. Its cadence and configuration were not changed.

## Reproduced compatibility gap

Official primary source:
<https://docs.salla.dev/order-items/list> (read 2026-11-14).
The published success example includes `id`, `sku`, `quantity`, and selected
`options`, with no `product_id` or `product.id`.

`salla_integration/order_items_snapshot.py` permits absent product identity.
`report_option_recovery.py` requires provider identities to equal exactly the
stored product identity. `cost_option_recovery.option_patch` forwards the raw
provider rows to that helper, so this valid response shape yields no patch and
the attempt can be recorded as unresolved.

Run the preserved, synthetic, offline reproduction from the repository root:

```bash
python -B docs/operations/MZ-SALLA-MISSING-OPTIONS-20261114/reproduce_item_identity_gap.py
```

It executes the actual pure functions, validates the real complete-items
contract, and shows that changing only the provider product identity changes
the result. It also checks contradictory identity refusal, input immutability,
preservation of existing prices/costs, and the narrow patch keys. No provider or
database calls are made. Final root run: exit 0. Initial harness import errors
(application bootstrap dependency and a missing helper namespace) were corrected
before that result; those failures are not product evidence.

This is a reproduction of an existing defect, not a passing regression for a
repair. No production source change or complete test-suite pass is claimed.

## Additional conditional hypothesis

The webhook monitor lists `order.products.updated`, while the order snapshot
handler accepts a smaller event set. If a missing choice arrives only in that
event, it would not be accepted by that handler. Captured events must establish
whether that happened; do not report it as the incident cause from code alone.

## Read-only evidence and limits

The investigation used scoped native/root order GETs, the worker status GET,
one health GET, and connected Salla read tools for store identity and the exact
reference. The Salla list response is lightweight; its detail response does
not contain items. Neither exposes the needed List Order Items response.
These reads did not invoke Mezan refresh/resync, apply a repair, or reimport the
order. A provider read may change its own read marker; this task does not claim
that Salla read metadata is immutable.

A full repository route search found no authorized exact-order HTTP read of
the saved webhook captures or the option-recovery attempt collection. Avoid
the review-detail GET with its default `local_only=false`, which can refresh
the source, and avoid unrelated lazy-cleanup endpoints.

The official database viewer authenticated successfully through the project
name and connected Preview. Production remains disconnected and requires its
separate connection credential. Preview data is not production evidence.
No database connection credential was revealed, extracted, copied, or logged. A transient SSO URL
appeared in a tool-generated tab title; it was not reused or persisted.

## Exact remaining evidence

Read only the authorized owner's exact order:

- Captured initial and later webhook events: event names/timestamps, native
  line/product identity, quantity, option field types/counts, processing result.
- `salla_cost_option_recovery` target hash: attempts/outcome/last and next attempt.
- Global scan lease/cursor for backlog context, without scanning business rows.
- Applicable catalog binding identity and current native/raw agreement.
- If available, already-retained List Items response identity shape; a newly
  requested provider read must remain read-only and never trigger Mezan import.

Do not use broad capture dumps, infer choices from price/SKU/name, bypass
identity refusal, clear retry state, or initiate a manual source refresh.

The smallest prospective compatibility fix would preserve native order/line
provenance and exact unique line/quantity agreement, and require stored raw/root
product identity agreement before handling an omitted provider identity. It
needs focused negative and transactional tests and independent review. No such
production patch has been applied by this investigation.

## Continuity

Task branch: `codex/salla-missing-options-investigation-20261114`.
Root owns integration and any separately necessary runtime actions. The prior
Draft #1909 is preserved at its existing remote checkpoint; it is not a remedy
for this missing-choice incident and was not modified or deployed.

Next action is scoped read-only production evidence through the official viewer
once its connection is available. Existing authorization persists; no manual
refresh, new scheduler, changed retry cadence, merge, or publication occurred.
