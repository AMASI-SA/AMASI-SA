# Production drift review — PR1238 versus completed Integration C3

Read-only independent review, 2026-10-02 Asia/Riyadh. No code/checkout edits,
merge, tests, services, Production access or financial mutation performed by
this reviewer. This file is the only review output. Existing completed9b451
Acceptance evidence remains attributed to its original exact source.

## Verified source identities and retrieval

- Completed Integration: `9b451cc03b4f8159483cbf58ef0fd128d24530e1`, TREE
  `1756c44f0f631c4f72dc24525c694601e0ee8011`.
- Previously integrated Production/base:
  `901568ccaaf510dc1f84d9c28f38d368d07dc64d`.
- New GitHub Production merge:
  `83363097d48e034dc7140a60c290efc684e1ffde`.
- [PR1238](https://github.com/AMASI-SA/AMASI-SA/pull/1238),
  title `fix(delivery): native evidence access and optional completion proof`,
  head `2e3776a17f7dcf4969dfffd7552110ce93d1d4a1`, is merged into
  hotfix/prod-snap-meta-final. Connector metadata records merged_at
  2026-10-01T21:53:48Z,12 PR commits and11 changed files. Compare old→new
  Production is13 commits ahead,0 behind, with those same11 files.
- Retrieved exact PR metadata, validated filename list and every changed Python
  file's patch through the GitHub connector. Also fetched immutable Production
  source ranges for the COD helper and optional-proof delivered branch.
  Native evidence/C3 and delivered TrackF contract read from local9b451 source.
  This proves Git changes, not deployed Production runtime identity.
- Root later reported successful non-destructive `--refetch` recovery and a
  read-only merge-tree showing textual conflicts in
  `backend/store_delivery_driver_app_routes.py` and
  `backend/tests/test_store_delivery_accounting.py`. I did not perform a merge
  or repository repair. The conclusions below concern semantic conflicts,
  which remain even if Git can merge other files automatically.

## Bounded classification

| Production change | Actual existing contract interaction | Classification / safe reconciliation |
|---|---|---|
| Native driver evidence-route access | `mobile_app_request_context.py` adds only `/api/store-delivery/evidence` to the purpose-bound driver prefix allowlist. Downstream evidence router still checks driver/owner/active assignment and image bytes; reads restrict same driver. Employee/other delivery prefixes remain denied. | **A/B wiring.** Reuse this delivered route allowance; preserve route-level identity and ownership checks and negative-prefix tests. No financial guard or new writer needed. |
| Optional operational delivery photo | Production delivered route changes mandatory proof422 into validate-if-present and stores nullable reference/URL; receipt rules for POS/bank remain conditional and unchanged. C3 separately requires actual amount+explicit true confirmation for positive cash COD, before Salla. | **A/B operational composition is possible**, but is not permission to remove TrackF's separate financial proof guard. Optional photo and mandatory cash attestation are different facts. Existing photo-backed C3 path can remain unchanged. |
| COD raw-provider null→zero correction | New operational helper recovers total for a particular uncollected COD provider shape; positive explicit remaining still wins; paid/refunded statuses suppress fallback. The assignment-list query now projects the raw facts needed by that helper. C3 reads the helper both before Salla and in the atomic commit. | **A/B source wiring for proven existing COD facts**, with explicit source/currency/ambiguity checks retained. Do not reinterpret actual physical cash as expected COD, guess an absent paid amount or introduce another financial writer. See concrete guard limits below. |
| Remove extra print-confirmation gate | `store_courier_domain.py` removes only carrier_label_print_confirmed check; label type, label-ready, completed stage and completed assembly still remain. Production tests now accept missing/false print confirmation. | Existing new **operational baseline**, not an accounting economic rule. Can be reconciled while preserving all remaining dispatch/driver/current-carrier checks. Do not represent an operational print bit as financial evidence. |
| Existing tests and release intent | Production replaces the prior three missing-proof HTTP cases with DTO optional-field assertions and updates print tests. Its release-intent-v5 describes a different source/base. | **B integration work.** Preserve C3 cash input/atomic rollback/idempotency/financial assertions; adapt conflicting operational-proof expectations rather than deleting financial tests. Do not copy the external intent as proof for a new merged Integration source. |

## Concrete semantic blocker: optional operational photo versus TrackF source contract

This is not merely C3's inherited request validation.

1. The shipped [TrackF contract](C:/Users/amasi/mz2-c-rich-shipping-20261001/docs/operations/MZ2-TRACK-F-20260930/CONTRACT-GAPS.md),
   under both `Production source/provenance trace` and `Driver responsibility`,
   explicitly requires the delivered assignment, owner/driver/order identity,
   collection outstanding snapshot **and bound proof** for driver responsibility.
   Its separate external-courier contract permits canonical Salla delivered
   without an uploaded receipt/photo; that exception does not silently extend
   to the store-driver contract.
2. Integration `backend/accounting_shipping_native_evidence.py:145-149` queries
   an owner/driver-matched `store_delivery_delivery_proofs` record in bound state,
   bound to this assignment, and raises
   `shipping_driver_bound_delivery_proof_required` if missing. Lines166-179 pin
   that exact proof with the assignment/collection and include its token in the
   financial source facts. This is a financial provenance guard, not a redundant
   frontend field rule.
3. C3 itself additionally binds the existing proof into the cash observation:
   `store_delivery_cash_evidence.py:42-62` requires it and seals it;
   `store_delivery_delivery_commit.py:94-96` revalidates it inside the atomic
   transaction; exact retry at37-41 refuses a changed proof. Those C3 metadata
   checks can be made compatible with an explicitly absent optional artifact
   without making that artifact a financial authorization.
4. The existing post-delivery recovery path does **not** close the financial
   gap. `store_delivery_payment_evidence_routes.py:229-240` permits proof uploads
   only for active assignments whose status is not delivered. Its proof endpoint
   at322-340 uses that same helper. C3's immutable cash replay rejects changing
   a previously sealed proof reference. No existing late-binding operation was
   found that can attach an uploaded photo after a photo-less delivery and then
   satisfy TrackF. No placeholder, fabricated photo, reset or historical rewrite
   is an acceptable substitute.
5. Production PR1238 explicitly says `No accounting writer/ledger/cutover changes`.
   Its optional delivered proof is consequently not an independent authorization
   to remove the financial source requirement. The parent confirmed that the
   user's C3 rule required actual amount/actor/order/time and any existing
   delivery reference, not a mandatory photo; that supports separating the cash
   observation from an optional artifact, but it does not rescind TrackF's
   established financial guard.

**Safe result without a new decision:** accept only the reconciled operational
contract and mandatory cash attestation, validate/bind any supplied proof, keep
photo-less Native recognition FAIL CLOSED/pending with the exact failure code.
Existing photo-backed deliveries still use the same TrackF writer and economics.
Never claim photo-less delivery has created a Native driver receivable.

**Decision genuinely required if photo-less deliveries must be financially
complete after cutover:** define which existing verified source replaces the
bound-photo requirement in the store-driver financial evidence contract, or
explicitly retain bound proof as a prerequisite for financial recognition and
accept the operational/financial hold. Changing evidence authority requires an
explicit contract decision; it is not a new accounting writer, but cannot be
inferred from the unrelated Production optional-photo change. A late-proof
amendment workflow is not currently delivered and must not be invented during
conflict resolution. This is new external-base drift, not withdrawal of the
previous C1–C5 authorization or invalidation of their completed9b451 proof.

## Least-change C3 compatibility requirements (not implemented)

- Preserve positive cash's exact `physical_cash_amount` and true confirmation
  requirements, including explicit zero actual cash; no default from expected COD.
- Represent absent optional proof consistently as null/absence across request,
  collection, immutable evidence and retry. Do not let None versus empty-string
  cause identical photo-less retries to conflict or create another collection.
  Preserve existing string-proof seals and old history unchanged.
- If a proof is supplied, retain owner/driver/assignment validation before Salla
  and again in the atomic callback, binding that exact uploaded row atomically.
  Skip photo binding only when it was explicitly absent; invalid supplied proof
  must still fail. Do not create an empty proof record/URL.
- Preserve fresh actor/driver and canonical order/amount reread after Salla,
  source concurrency checks, insert-only observation, exact retry without another
  Salla call/observer, rollback and restricted operational capability.
- Keep Native observer and bound-source guard as they are unless the financial
  contract decision above is supplied. Paused423, unready503, SSOT/write-control,
  Legacy denylist and separation of expected COD/actual cash/fee stay unchanged.
- Preserve POS/bank receipt evidence and their separate accountant review/bank
  settlement rules. Optional delivery photo does not make a POS receipt optional.

Focused verification required on any future reconciled source: proof-present
old path; no-photo cash with explicit confirmation; missing-confirmation rejection
before Salla; invalid/foreign supplied proof rejection; repeated/concurrent
photo-less request with one immutable capture and no financial effect;
post-Salla identity/amount/proof changes and atomic rollback; Native missing-bound-
proof failure; no later upload admitted silently; unchanged423/503/Legacy guards.
These are proposed checks, not tests executed by this review.

## COD helper: concrete source limits to preserve

Production `_uncollected_cod_total_fallback` at
`store_delivery_payment_evidence_routes.py:68-129` is narrower than a generic
order-total fallback, but not identical to the current Order Engine contract:

- Existing `order_engine/mapper.py:1149-1162` requires SAR for this specific
  synthesized-zero recovery. PR1238's helper has no currency check. Example source
  shape: raw COD + remaining_action.remaining_amount=null/has_remaining_amount=false,
  paid0 and root total100 in USD passes the new helper's conditions, while C3
  `build_cash_evidence` always records SAR and Native facts independently reject
  non-SAR (`accounting_shipping_native_evidence.py:58-62`). Do not silently label
  such a new fallback amount SAR in the reconciled C3 path.
- Its `paid_value = money(order.get('paid_amount') or 0)` treats missing paid
  evidence as zero and does not compare raw paid amounts to normalized root.
  A missing root paid field with raw remaining_action.paid_amount50 can therefore
  meet the fallback's uncollected assumptions if statuses are not explicit paid/
  partial. Current `order_engine/shipping_label_service.py:361-383` demonstrates
  the already delivered stricter distinction: missing paid needs explicit unpaid
  status, otherwise fail; source currency and totals must be provable. Existing
  TrackF raw source contract likewise rejects conflicting raw decimal facts.
- Preserve positive explicit remaining values and paid/refunded exclusions, and
  include the added raw/payment projections on the list path so displayed expected
  COD agrees with delivery commit's reread. Actual cash remains independent.
- The examples above are source-inferred counterexamples, not runtime executions
  or a claim that Production contains those records. Tightening the adapter to
  existing explicit-currency/no-guess/consistent-source requirements is ordinary
  authorized integration validation. It must not become a new guessed rate,
  default account, broad root financial fallback or rewritten historical balance.

## Review outcome

A safe A/B composition exists for the nonconflicting operational corrections and
photo-backed C3/TrackF flow. Simply choosing one entire conflicting route/test
file would lose a delivered contract. The meaningful unresolved decision is the
financial provenance of newly permitted photo-less driver deliveries: there is
an explicit bound-proof financial guard and no delivered later-proof closure.
Keep those financial actions FAIL CLOSED until the contract is explicitly decided.

No claim of full Release readiness against new Production83363097 is justified
from the earlier9b451 acceptance/CI. Preserve that evidence, reconcile only under
actual authority, and rerun affected/full gates on the resulting immutable source.
Production writes0; Merge/Deploy/Opening Post/ActivationNO; write-control unchanged.

