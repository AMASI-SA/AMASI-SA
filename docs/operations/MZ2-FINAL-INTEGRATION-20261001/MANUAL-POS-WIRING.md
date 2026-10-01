# Manual POS review — authoritative integration contract

The user explicitly clarified that the accountant's review of the driver's
bound POS receipt is the approval authority. An external terminal API or
processor-success producer is not required. This supersedes the earlier
external-source C blocker; it does not authorize any production write, posting,
activation, deployment, new account type or new financial writer.

The user separately approved explicit selection of an existing documented MZ2
receivable: `asset / exact typed-fact entity_id / other_receivable`. No default,
name matching, inferred identity, new POS registry or account creation occurs.
The existing other-receivables report classification is retained.

## Existing chain and boundaries

1. The existing driver submission binds receipt bytes and creates a pending
   review. It does not approve itself or settle the driver's responsibility.
2. The existing accountant review page reads the existing onboarding typed-fact
   API. The accountant explicitly selects the documented SAR receivable,
   checks the bound receipt, enters its exact amount and optionally its
   transaction reference. The existing approval request carries
   `destination_financial_id`, `pos_reviewed_amount` and optional
   `settlement_reference`. No endpoint is added.
3. `review_driver_payment` enforces its existing fresh actor, permission,
   owner, writer-state, P02, opening and pause guards. Its existing destination
   port validates the bound receipt context and exact amount, and resolves the
   selected owner-scoped active documented typed fact. The exact stored identity,
   category, currency and creator provenance must match its existing contract.
   Missing identity, opening or evidence fails closed without mutations.
4. Only approval posts, through the existing Track F native writer:
   **Dr selected POS receivable / Cr driver COD receivable**. It never debits
   Bank, changes sales/tax, offsets delivery fees or invents a financial account.
5. Rejection records the review decision but creates no journal or settlement;
   driver COD responsibility is unchanged.
6. Later `settle_pos_to_bank` retains its existing native movement, canonical
   bank, balance, atomicity and replay checks. It reuses the exact sealed POS
   tuple: **Dr Bank / Cr selected POS receivable**. It does not touch the driver
   or delivery fees again.

The selected identity, display name, original typed-fact reference/evidence,
receipt hash/token, optional transaction reference, reviewed amount and actor
are saved in the existing sealed review event and native journal metadata.
The existing journal table identifies POS movements using that explicit
destination metadata and shows the name and exact tuple. Unrelated
`other_receivable` rows are not labelled POS.

## Replay and evidence consumption

Approval remains one atomic owner transaction. Identical review retries return
the existing journal. The optional POS amount is omitted from the request hash
when absent, preserving existing bank and rejection retry fingerprints.

An explicit manual transaction reference is unique within the owner and cannot
be reused by selecting another receivable. With no reference, the bound receipt
hash supplies the source identity. A separate receipt-hash reservation prevents
the same image being reused with a changed reference or destination. These
reservations, approval, receipt state and journal commit or roll back together.
No processor namespace is inferred from the selected general receivable.

Later bank settlement retains both its existing request fingerprint and atomic
single consumption of the bank movement. A changed request key cannot consume
the same movement twice or exceed the approved POS balance.

## Verification and release identity

The new real-Mongo HTTP suite is
`backend/tests/test_mz2_driver_pos_manual_review.py`; it uses the actual adapter,
existing typed-fact creator, synthetic native opening and canonical bank port.
It enforces a credential-free loopback replica URI, unique disposable databases
and the native suite's zero-Legacy command monitor. No external success adapter
is injected. Separate existing consumer seam tests remain clearly labelled.

An unchanged-source baseline passed54 existing driver/bank tests. The new manual
approval test failed against copied baseline252d810 modules with422 for the new
reviewed-amount payload, demonstrating the former closed path. Exact final test
counts, logs, HEAD/TREE and full CI are recorded in STATUS and Issue1006 after
integrated verification; source/intent identities are never self-referential.

This source changes a previously frozen candidate. A new source/build/intent
pair is required, with the original reviewed pair preserved. The unchanged
Production release protocol, 423 gates, write-control and all other C/UAT/SmokeB
holds remain in effect. Production financial writes=0; Merge to Production,
Deploy, Opening Post and Activation=NO.
