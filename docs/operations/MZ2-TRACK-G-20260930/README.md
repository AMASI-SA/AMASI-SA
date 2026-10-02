# Track G delivery — native setup and reports

Status: **MZ2_ONBOARDING_SSOT_REMAINING_BLOCKED_BY_EXACT_GAP**.
The implemented changes are reviewable; cross-track financial identity and
unsupported classification contracts remain explicit blockers. This is not
activation, a release candidate certification, or Smoke B proof.

## Source and boundaries

Fresh fetched Production HEAD: `5a7b44b71c6c9974aba358493b3267a47d6e6314`.
Production TREE: `87f9a44af5dc7d00fbc62417fb17e2f6273d8d93`.
Branch: `codex/mz2-onboarding-ssot-6-11-13-14-reports-20260930`.
Final branch HEAD/TREE, Draft PR and CI snapshot are recorded in Issue #1006.
Independent worktree; original user checkout and its unrelated modifications
were preserved. No commits or files copied from #1212/#1209/#1214/#1215/#1216/#1217.

## Delivered contracts

- Stage 6: `mz2_external_persons_v2`, owner scoped native UUID, display name,
  person type/reference, status/version, creator/updater and embedded audit.
  `POST /api/accounting-module/onboarding/external-persons` uses fresh actor and
  opening-view/drafts-manage permissions. No legacy copy or financial write.
  Opening uses `external_person` plus that exact ID and existing `receivable`
  subaccount. Active V2 employee/supplier registries replace legacy discovery.
- Stage 11: immutable effective fee schedules serialized by Mongo document CAS;
  exact one date/currency/provider resolution. Selected policy evidence is
  verified against original owner-scoped source-file bytes during preview/review.
- Stage 13: real recurring invoice selection, explicit currency/evidence adapter,
  calendar-day calculation and immutable source snapshot; paid before cutover
  and future coverage required. Missing payable facts cannot become prepaids.
- Stage 14: typed independent accrued/other payable, receivable, Sales VAT and
  Input VAT facts. Manual exceptional prepaid requires separate contract evidence.
  Every active owner-scoped fact at the current cutover must be selected and
  represented by an exact opening line; an empty section cannot omit liabilities.
- Reports: native ledger-only reports and entity statements, canonical identity
  checks, visible unresolved blockers, null missing totals and no VAT netting.
- Old Opening mutations are quarantined; preserved engines are internal only.
- Stage 15 names incomplete sections, evidence/identity/policy/source/report
  blockers. Stage 16 remains locked, with Smoke B and owner authorization gates.

Setup routes live in an explicit separate router. The only new setup write
collections are `mz2_external_persons_v2`, `mz2_provider_fee_policies_v2`,
`mz2_prepaid_selections_v2`, and `mz2_opening_facts_v2`, plus existing session
metadata. Financial handoff retains the owner pause barrier. No new setup path
can call ledger posting, activation, cutover mutation or an external provider.

## Prepaid calculation evidence

Native invoice dates are inclusive. The requested 21 Aug 2026 through
21 Aug 2027 example has 366 contract days. With 3,660 SAR paid on 21 Aug and
1 Oct 2026 cutover: 41 consumed days = 410 SAR; 325 remaining days = 3,250 SAR.
The existing annual-cycle producer ending 20 Aug 2027 has 365 days. Dates are
not silently shortened. See [full contracts](../../track-g-contracts.md).

## Integration dependencies and exact gaps

1. Employee V2 on this Production creates `financial_entity_id=None`. The
   operating V2 ID cannot silently become an accounting ID. The catalog stays
   usable, but review and reports block until an explicit exact native financial
   binding exists. #1209 is the integration dependency; its code was not copied.
2. Native ad bindings are absent. Ad opening/report classification blocks with
   `onboarding_native_ad_binding_dependency` / unresolved native identity.
3. Deposit has no proven Opening category. Requests are rejected; no substitute
   category is invented. Ordinary documented receivables use their own contract.
4. Settlement must consume the new fee resolver at operation date; this track
   supplies the port and does not modify settlement financial posting.
5. Additional runtime system categories require explicit native report adapters;
   unknown categories remain visible blockers rather than guessed classifications.
6. Inventory physical approval (#1214), canonical bank/provider work (#1212),
   supplier runtime extensions (#1215/#1216), and shared UI (#1217) are separate.
   Current native supplier registry IDs remain usable; future integration must
   reconcile any canonical identity changes explicitly.

Expected conflicts are limited to central onboarding definitions/compile/readiness,
identity catalogs, the service client and two small wizard integration hooks.
The new setup editor is separate. No Stage 10 inventory file was changed.
Historical financial-position UI is labeled LEGACY diagnostic/read-only.

7. **Runtime native-writer blocker:** Settlement, refunds and P02 shipping still
   invoke `ledger_core.post_txn_group` (legacy writer), while their balance
   authorization now correctly requires native MZ2. Legacy-active rejects the
   balance read; V2-active rejects the legacy writer. Broad regression exposes
   14 genuine integration failures (2 receivable/refund/settlement; 12 shipping
   fixture activations). Native posting/duplicate-detection migration must precede
   integration. These tests are not weakened and no fallback is restored.

## Evidence map

- [Setup contracts and producer map](../../track-g-contracts.md)
- [Report source map and supported identities](../../track-g-reports.md)
- [Full registered old-route reachability](track-g-old-routes.md)
- `.github/workflows/mz2-track-g-ssot.yml`: disposable Mongo backend and UI CI.
- `TEST_RESULTS.json`: fresh local test counts, environment, source hashes.

Production financial writes = 0. Merge = NO. Deploy = NO.
Production Opening Post = NO. Activation = NO. Release Guard = NOT RUN.
No cutover change. Tests use isolated random local Mongo databases and synthetic
native journal fixtures only. No production data, tokens or evidence exported.
