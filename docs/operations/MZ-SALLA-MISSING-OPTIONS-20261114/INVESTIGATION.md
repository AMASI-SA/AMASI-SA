# Missing incoming Salla customer options: confirmed intake ordering defect

## Scope and conclusion

The owner requested the reason an order arrived without customer choices,
without a manual Salla refresh, within an already authorized root-fix task.
This is separate from accepted-snapshot projection correction #1908 and its
unfinished manual repair surface #1909.

**Exact saved Production captures establish the cause:** a sparse
`order.status.updated` was accepted first. Four milliseconds later,
`order.created` arrived with five options on each of the same two native lines.
Its provider update time was one second older, so the source fence returned
`stale_salla_snapshot` before its persistence callback. The useful choices
were discarded along with the older full snapshot.

At the initial read, native Order V2 and root products both had empty choices,
revision 1 and the first-arrival source clock. This was upstream of the screen
and cost resolver. No live order, owner, merchant, product or customer IDs,
credentials, customization text or raw payloads are retained here.

## Exact-order evidence, anonymized

The official database viewer connected to Production through secure
user-entered authentication. Queries were scoped to the authenticated owner
and exact order, merchant and exact captured order identity, and the exact
SHA-256 recovery key. Edit mode remained off.

| Relative arrival | Event | Source version | Item choices | Processing result |
| --- | --- | --- | --- | --- |
| First | `order.status.updated` | Newer by one second | Choice fields absent on both lines | Accepted; order created |
| Four milliseconds later | `order.created` | Older by one second | Five option records per line | Rejected as stale |

The captures agree on native order/reference, complete native line membership,
explicit product identity and quantity. Status uses `product_id`; creation
uses the matching `product.id`. All shared commercial fields match: price
without tax, discount, tax and total, including currencies. Creation adds
original price. Choices were not inferred from prices, SKU, name or position.

The exact recovery hash returned zero documents at inspection time. A fresh
worker heartbeat showed `salla-cost-options: true`, but does not establish a
target attempt or successful restoration.

A later stored read showed five options per line and revision 2.
`orders_v2_salla_*` markers identify the central Order V2 refresh path; they
do not identify the initiating caller or actor. Restored native choices equal
the original creation capture. The live order page subsequently displayed the
stored size selections. This later restoration is not a preventive fix.

The task did not click Salla refresh or invoke a Mezan refresh/resync route.
The three diagnostic GETs used here do not invoke central refresh:
`/api/orders?search=...`, `/api/orders-v2/{number}`, and
`/api/integrations/qoyod/worker/status`. Do not attribute the later refresh
to a particular person or to the automatic choice-recovery worker.

## Source and runtime identity

- Reviewed production merge: `29ec1e07eb7d1a23647cadff5bac26149accfa99`.
- Observed live source: `2cf4d70d7e0c23c11048cad5c610e8c0c60d7eeb`.
- Runtime release:
  `rg5-5c3980b0f83f3859023070ef846486e877849ca7ca848367a181995ca32c1d5e`.
- Source-to-merge diff changes only the release intent.
- One health observation reported verified identity and matching runtime
  evidence. This task did not perform a new official three-probe release
  verification.

## Confirmed source mechanism

`fulfillment_v2_routes.persist_component_source_snapshot` rejects an older
provider version before calling persistence. Therefore
`orders_db._merge_salla_raw_snapshot` never receives the delayed creation's
choices. Its existing protection of already-known choices cannot help.

`salla_integration/webhook_order_sync.py` persists accepted events without
provider hydration. `orders_db.py` projects products from the same accepted
merged raw source. `order_engine/mapper.py` reads native choices separately.
The two empty consumers therefore agree with the rejected rich source event.

The cost resolver correctly leaves an unknown bound choice incomplete.
Missing selection evidence must not become a zero-cost selection.

Run the source-level mechanism reproduction:

```bash
python -B docs/operations/MZ-SALLA-MISSING-OPTIONS-20261114/reproduce_stale_creation.py
```

It extracts the actual functions with AST, uses synthetic values and a minimal
in-memory database/owner seam, and verifies stale rejection, zero persistence
callback calls, unchanged current document, and downstream same-line option
preservation without cross-line inheritance. It uses the standard library;
there is no application bootstrap, provider request or database write.
This is not full webhook/Mongo integration or a passing repair regression.

## Separate compatibility finding, not the incident cause

Official primary source:
<https://docs.salla.dev/order-items/list> (read 2026-11-14).
Its success example contains native line identity, quantity and options but
may omit product identity.

`order_items_snapshot.py` permits that shape in its complete-items contract.
`report_option_recovery.py` requires an explicit matching provider product
identity. `cost_option_recovery.option_patch` consequently refuses the
otherwise complete no-product-ID shape.

```bash
python -B docs/operations/MZ-SALLA-MISSING-OPTIONS-20261114/reproduce_item_identity_gap.py
```

Fresh root and independent runs exited 0: omitted product identity gives no
patch; matching identity restores choices in memory; contradiction is refused;
original data, prices and costs stay unchanged. This exercises the real
contract and pure functions, not asynchronous HTTP acquisition.

The target's exact recovery key has no record. Its List Items response shape
was not obtained. The captured webhook ordering already explains this
incident; do not substitute the adjacent compatibility finding for that cause.
That separate repair is outside the current narrow prevention patch.

The monitored but unaccepted `order.products.updated` event is another
conditional source observation. The exact captures do not support it as the
cause here.

## Preventive repair contract under review

Preserve the full-snapshot stale rejection. Attempt separate option-only
enrichment for a verified delayed creation only when current raw choice fields
were omitted and exact order/line/product identity, quantity, membership and
existing monetary evidence agree. Protect explicit empty/current choices,
ambiguous identity, changed lines, cancellation and authoritative-refresh
blocks.

Use the current source digest, owner transaction and component fence. Preserve
newer status, source clocks, totals, prices, shipping, historical costs,
financial facts, preparation and stock. Repeated delivery must be a no-op.
No provider request, manual replay, scheduler, scan, retry reset or cadence
change is required.

Keep the full event result stale and report enrichment separately. Do not
invoke full-sync attribution, financial delivery observers or auto-routing
from that branch. Capture audit must describe option-only mutation truthfully.

Focused regression, identity/currency/quantity/cancellation negative tests,
concurrent-source protection and real owner-transaction verification are
required before readiness. No preventive source patch, merge or publication
has occurred at this evidence checkpoint.

## Access and preservation

Read-only evidence used saved owner APIs, worker status, health, connected
Salla reads and the official Production database viewer. Connected Salla
detail did not contain items; its own read-marker semantics are not claimed
immutable. The task did not manually reimport or repair the live order.

Avoid default review-detail GETs that may refresh Salla and unrelated
lazy-cleanup routes. No broad record dumps, retry-state clearing or inferred
choices were used.

No database credential was extracted, copied or logged. A transient SSO URL
appeared in a tool-generated tab title; it was not reused or persisted.
Production access subsequently used the secure credential entry capability.

## Continuity

Task branch: `codex/salla-missing-options-investigation-20261114`.
Draft PR: <https://github.com/AMASI-SA/AMASI-SA/pull/1923>.
Root owns integration/release actions. Prior Draft #1909 remains untouched.
Issue #1006 is comment-limit blocked; this task status and Draft provide the
supplementary recoverable record.

Next: implement and independently verify the bounded delayed-creation
prevention under the existing root-fix authorization. No live order replay or
manual refresh. Production code was not changed by this evidence checkpoint.
