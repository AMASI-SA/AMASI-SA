# C3: driver confirmation at delivered and explicit handover matching

The latest user decision supersedes the accountant-opening-attestation proposal:
the driver confirms actual cash at the existing COD delivery action. Expected
COD remains the full authoritative order amount, counted once. Actual cash is
a separate observation. A difference is displayed, never automatically posted,
netted with fees, or used to reduce driver responsibility.

## Delivered implementation

- Existing driver delivery route requires an explicit exact amount and true
  confirmation for positive cash COD. Missing confirmation fails before the
  Salla status request. Noncash and zero-outstanding delivery do not fabricate
  physical cash. Existing required delivery proof is uploaded separately from
  a bank/POS receipt.
- Restricted `driver_cash_delivery` operational transaction commits the original
  expected collection/earning, assignment/order status, immutable cash evidence,
  proof binding and event together. Fresh actor, driver, order amount and proof
  are checked again after the external response. Failure rolls all local changes
  back. The external Salla request cannot be part of a Mongo transaction.
- Identical delivery retry reads the same evidence. Conflicting actual amount,
  actor or proof fails closed. A retry does not re-run Salla or the observer.
- The original Track F delivery observer still runs after a successful initial
  commit. It consumes expected COD and original fee, never actual cash. Its
  existing 423/identity gates and pending inbox remain unchanged. Thus the new
  observation creates no journal; the pre-existing delivery accounting behavior
  is retained and is separately tested with COD500, actual450 and fee20.
- Driver and accountant readers show captured observations, expected/actual
  totals, variance, current eligibility and explicit coverage gaps. Cancellation
  or a source change preserves the observation and prevents new matching. No
  historical or opening cash is inferred from COD or a missing record.
- An accountant explicitly allocates observations to one existing verified
  Native cash-settlement event, or to an existing operational COD remittance
  with its original provenance. The latter is labelled operational evidence,
  **not proof of a Native financial settlement**. No Legacy account lookup or
  old settlement writer is called. Bank/POS reviews, fees and net settlements
  are excluded.
- Matching is append-only metadata in `mz2_driver_cash_reconciliations_v1`, under
  the existing owner serialization and a restricted insert-only capability.
  Exact retries are idempotent; duplicate target, over-allocation, wrong owner,
  stale permission, changed evidence, earlier handover and invalid journal fail
  closed. A later journal reversal marks proof incomplete without inventing a
  physical cash return or deleting history.

## Evidence shape and path

`store_delivery_collections.physical_cash_evidence` stores schema
`mz2.driver.physical_cash.v1`, existing collection/assignment/order/driver IDs,
driver name, delivered status, COD amount, confirmed actual amount, variance,
SAR currency, aware confirmation timestamp, confirmation actor, original delivery
proof reference, seal and `financial_effect=none`.

```text
Delivered COD order (full expected responsibility)
  -> driver's immutable actual-cash observation
  -> captured custody and variance, with source eligibility
  -> explicit allocation to existing cash handover
  -> reconciliation metadata; no new financial posting
```

## Verification and limits

The Real Mongo suite covers the user's cases a–h plus owner isolation, fresh
authority, concurrent capture/matching, post-insert/event rollback, caught
financial-capability escalation, tampered/reversed Native journals and original
COD/fee economics. All databases are disposable on the loopback replica set.

The first adjacent run found three old cash-delivery fixtures with no explicit
cash confirmation (115 PASS/3 FAIL, 422). They now supply the newly required
confirmation and persisted driver. Their original journal, fee, pause, observer
and proof assertions remain intact. This is a fixture update for the approved
input contract, not removal of a financial assertion.

Fresh counts, exact source HEAD/TREE, CI and browser evidence are recorded in
STATUS and Issue1006/PR1237. Source acceptance is not full business UAT. Original
historical source-gap audit is retained with a supersession note.

Production financial writes=0. Merge/Deploy/Opening Post/Activation=NO.
Write-control and financial 423 guards unchanged. Operational serialization
may increment its existing revision token; no pause/activation flag is changed.
