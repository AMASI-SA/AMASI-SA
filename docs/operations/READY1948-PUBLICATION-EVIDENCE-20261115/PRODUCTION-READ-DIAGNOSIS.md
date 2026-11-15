# Ready follow-up: production read diagnosis

## Outcome

**Current blocker identified; earlier failed refresh remains unresolved.**

Read-only inspection of order `299781816` on 2026-11-15 established that its canonical Salla status changed to `completed` at **20:58:08 UTC / 23:58:08 Asia/Riyadh**. Assembly still shows **0 of 5 Ready**, and all five pending pieces display **«مرحلة الطلب لا تسمح بإكمال المنتج الآن»**. The deployed Ready policy requires `in_progress` for a new piece completion.

The component source hold is currently clear: source revision **14**, `component_pending=false`, and an accepted matching lifecycle. This does **not** establish that PR1948 repaired the earlier failed refresh, nor that any piece became Ready.

## Authorization and read scope

The user explicitly authorized opening the production database manager and reading matching records for this order only. The Mongo URL was supplied through the secure browserAuth form; credentials were neither exposed nor copied by the assistant.

Production context was verified through the manager's **Production Databases** heading. A unique `unified_orders` match used both the reference `299781816` and native Salla order ID `800420115`, stored as strings. The resulting owner was used in subsequent order/lifecycle filters. Webhook reads used the Salla store `1673234356` and the exact order reference. Owner identifiers, customer data, raw payloads, and credentials are excluded from this evidence.

This diagnostic investigation made **zero** database, order-status, Ready, receipt, shipping-label, financial, source-code, or publication mutations. The earlier authorized PR1948 publication remains separately recorded in the existing evidence.

## Observed state and consistency

| Evidence | Observed value |
|---|---|
| Salla connector | Order ID `800420115`, reference `299781816`, status `completed` |
| Salla status history | Completed activity at 23:58:08 and 23:58:10 Asia/Riyadh |
| Unified source clock | `2026-11-15T20:58:08.000000+00:00` |
| Unified source revision | `14` |
| Component pending | `false` |
| Lifecycle state | `legacy_operational_pre_g47` |
| Lifecycle accepted / retry / cancelled | `true / false / false` |
| Lifecycle revision / generation | `14 / 3` |
| Lifecycle update | `2026-11-15T20:58:19.791414+00:00` |
| Currency/catalog recovery audit fields | Absent |
| Final watermark consistency read | `2026-11-15T21:06:05.285Z`; unchanged |
| Final assembly observation | `2026-11-15T21:06:33.320Z`; 0/5, five disabled Ready buttons, five order-stage blockers |

The raw provider `updated_at` has the Asia/Riyadh clock object with the same instant as the source watermark. The original native `date` remains November 13. These fields do not establish a clock mismatch as the earlier rejection cause.

## Source update provenance

Six unique exact-order webhook captures were read and checked against visible event identities. The two newest are:

| Event | Received (UTC) | Outcome |
|---|---|---|
| `order.updated` | `20:58:10.205` | Synced; full order from webhook; no retry required |
| `order.status.updated` | `20:58:19.374` | Synced; full order from webhook; no retry required |

Both expose attempted auto-fulfillment with no promotion/reversion and a reported route stage of `in_progress`. That route-stage field does not override the canonical Salla status `completed` used by the Ready guard. The latest decision has no listed blockers. The captures do not expose a component-lifecycle subresult for these two events.

The older November 13–14 captures contain **stale-related component failures**, including `stale_ignored` and `component_source_event_stale`. They do not record the rejected predicate of the subsequent user-initiated refresh.

Salla history and these captures establish the status/source transition. They do **not** establish which person or process initiated it.

## Limits and earlier evidence

The exact revision of the earlier failed refresh was not captured. No immutable raw/lifecycle/approval snapshot of that state was available for replay. The revalidation path returns a generic refusal without durably recording its exact failed predicate.

Historical evidence at commit `71fd5d570f8880ad377269ad136915552e98d480`, under `docs/operations/OPTION-PRICE-CURRENCY-PROOF-20261115/`, describes the pre-fix source comparison rejection at watermark revision 6 and the subsequent detail-recovery source work. Its private inputs were intentionally not committed. That historical proof explains the prior change's scope; it cannot be transferred to the later failed refresh.

Independent source review accepted the interpretation here: **current status blocker proven, prior refresh cause unresolved, no successful business recovery claimed**.

## Next decision

Clarify whether moving this order to **completed** was intended before any operational status change. The current authorization was read-only. Repeated refreshes alone cannot make a pending piece eligible while the order remains completed. A controlled return to the intended preparation stage, if authorized, would allow the current Ready eligibility to be read again; it must not be represented as a proven fix for the earlier source-revalidation failure.

Any corrective source release must preserve the provider release that advanced production to `236a428364bc8163a684795ed1be87e237476f18` through PR1950. The provider owner retains its publication continuation until an explicit final inactive handoff; this investigation did not touch its lease or shared source.

## Evidence

- `production-read-diagnosis.json`: sanitized technical fields, Salla status readback, six unique capture summaries, final consistency and UI readback.
- Earlier `after-user-refresh-readback.json`: 20:42:25 UTC UI evidence of the original source-reconciliation blocker.
- Existing publication receipts: PR1948 was published and verified earlier; publication success is separate from the unresolved business recovery.
