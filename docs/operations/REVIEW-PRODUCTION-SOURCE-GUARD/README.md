# Production Review Completion representation guard

## Scope and evidence

Forward candidate from Production `a7977f4cfc1ef0a721fc25d783661332f32fe3b6`.
No rollback, old-operation recovery, backfill, live Salla request or deployment.
The nine historical incident source pairs are incomplete: their precise trigger
remains UNPROVEN. The synthetic contracts below prove a reproducible class only.

Deployed source_fingerprint includes the raw shipping dictionary. Actual
`salla_refresh.py` adds `shipping.company_name` and duplicates the shipping
address. A refresh during provider I/O can therefore change that fingerprint
without changing the order's business facts. Revalidation after durable provider
confirmation rejects before workflow/event commit. The unchanged resume worker
correctly treats the conflict as requiring review; it cannot waive the guard.

## Narrow normalization contract

- Versioned approval contract 1, snapshot schema 1, normalization 2.
- `salla_refresh.py:501-508` proves exact address duplication and company-name
  enrichment; `salla_shipping._carrier` reads both scalar company spellings.
- Populated equal company/company_name normalize. Company objects keep their
  id/code/logo and all other metadata. Conflicts and empty aliases reject.
- Nonempty identical root shipping_address / nested shipping.address normalize.
  Null, empty, malformed and conflicting addresses are not declared equivalent.
- Provider clocks/status are separately guarded; `date.updated` is a documented
  provider clock (`component_provider_version`). Only parseable timestamp strings
  normalize; creation and unknown date facts remain guarded.
- No shipping-method/customer/product-ID aliases are guessed. Item, option and
  selection array ordering remains significant. Bool/string/number are distinct;
  numeric JSON spellings preserve exact Decimal value without float rounding.
- Every unknown source/DTO field is retained by fingerprint and changes reject.
  Known DTO status/ingestion/attribution projection fields have explicit inventory.
  Actual mapped DTO business facts are independently frozen and compared.

## Completion and compatibility

New operations freeze the snapshot once before provider I/O. A separate contract
marker prevents missing/corrupt snapshot downgrade to legacy hashes. Existing
unversioned Production operations keep their original checks; no approval is
reconstructed or promoted, and requires_review is not automatically recovered.

Acceptance config fencing, explicit reapproval identity, component ticket checks,
revision, generation, cancellation, product/payment eligibility, provider readback,
lease/concurrency, timeout and transactional workflow/event/result stay enforced.
A real source-document increment in each validation transaction fences concurrent
writers which do not acquire the owner lock. New explicit acceptance reapproval
compares the old business basis under old acceptance before validating the new
explicitly confirmed acceptance; it cannot silently substitute a new approval.

G47 prerequisites run before snapshot creation, preserving specific missing
creation/canonical-document errors. The legacy test now supplies an actual full
source; an additional test rejects missing source before provider/operation.
No component or financial effects are added to the legacy scan/pack path.

## Exact affected files

- backend/order_review_completion.py: snapshot lifecycle, strict contract marker,
  source transaction fence, diagnostics and component evidence; same completion.
- backend/order_review_business_snapshot.py: pure normalization/comparison.
- backend/tests/test_review_completion_business_snapshot.py: pure contracts.
- backend/tests/test_review_completion_representation_integration.py: actual
  refresh/webhook/mapper/completion/worker/catalog with fake provider transport.
- backend/tests/test_g47_operational_boundary.py: complete-source fixture and
  missing-source rejection, with original assertions retained.
- .github/workflows/review-completion-resumable.yml: adds the two new test files;
  no trigger, guard, skip or suppression changes.
- This documentation; no Accounting, Mobile, Shipping implementation changes.

## Comparison (isolated MongoDB 8.0.12 replica set)

Identical actual refresh-during-provider contract, synthetic new order:

| Code | HTTP | Operation | Reviewed workflow | Completion event | /reviewed |
|---|---|---|---|---|---|
| Production a7977f4 | 409 source_changed | provider_confirmed | 0 | 0 | absent |
| Candidate | 200 | completed | 1 | 1 | visible |

The same-provider-facts source hashes differ with the deployed raw comparator.
Candidate approval remains immutable. Auto Resume + two workers + manual retry
on refresh/order.updated facts retains exactly one operation/workflow/event.
Business, unknown, conflicting alias, acceptance, cancellation and component
changes reject; stale source writes after transaction snapshot cannot commit.
These are contract tests, NOT replay of any historical Production order.

## Validation

Final same-HEAD evidence is recorded in PR #1270 and continuation Issue #1006.
Full G47 and Review suites run against isolated loopback Mongo replica sets,
with standalone Mongo only for explicit standalone-rejection tests. Production
credentials/config are not loaded; provider transport is mocked. No live UAT.
The existing Security/provenance and release gates remain separate and are not
waived by Review test success. This candidate must not be merged/deployed here.
