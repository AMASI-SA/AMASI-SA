# Owner review of closed advertising spend

## Problem and scope

An advertising platform can restate native spend after a day has closed. The
operational reducer correctly holds an unapproved financial change, but the
native adapter has no authority to approve it. This change supplies a bounded
owner review through the existing visible refresh and aggregate compare-and-swap
(CAS). It does not create compensating cash movements or a separate posting path.

The owner opens **Tools → Review closed advertising days**, selects one configured
account and at most 31 days, then explicitly requests a preview. The panel is
dormant before it is opened. It shows the original day, native currency, old and
new rounded amounts, every funding part, and any SAR conversion difference with
the pinned FX evidence. The owner selects eligible days, gives a reason, confirms
the review, and submits one approval. Opening the panel or preview never submits
a financial update.

## Authority and evidence

- The preview and saved receipt are owner-only reads with current scope checks
  before and after their work. Native reads use a consistent Mongo snapshot,
  owner predicates, projections, a 12-second read budget, cursor limits and
  per-query deadlines. A missing or ambiguous account binding blocks approval.
- The existing normalizer verifies each native close proof for Snapchat, Meta,
  TikTok or Google Ads. Snapchat retains its distinct report-date, base-spend and
  source-observation fields. No platform request or native source write is added.
- The preview digest binds the aggregate revision/header, exact account/binding,
  old day and obligation parts, raw native evidence, and proposed normalized
  parts. The client supplies selection, digest, revision, request ID, reason and
  explicit confirmation. It cannot supply financial amounts, approval flags or
  correction fact IDs.
- Only a saved closed day with complete mature native close proof is eligible.
  The original identity, date, currency, funding policy, split and baseline must
  remain compatible. Incomplete, duplicated, stale or conflicting evidence is
  shown as blocked. Foreign-currency old and new parts require positive, dated,
  complete pinned FX evidence and a finite SAR amount. Changing the FX basis is
  outside this workflow.
- Native precision alone is not a financial change. Every rounded funding part
  and rounded SAR part is compared. A native total with unchanged cents can still
  require review when a hybrid split or SAR converted amount changes. SAR identity
  observation timestamps alone do not create a conversion change.

## Atomic approval and replay

Approval is an optional input to the existing `/operational-balances/refresh`.
Normal refresh behavior, lease ownership, authorization, cancellation, native
acquisition and recovery proof generation remain in that path. Approval requires
the current owner at every existing authorization gate.

After ordinary source collection, the service reads the selected native evidence
again and recomputes the preview against the exact prior state. It compares the
already collected snapshots with that evidence and injects server-generated
approval flags only for the selected unchanged review. The reducer validates all
amount/funding/retirement preparation before changing the day or correction fact.

The service verifies the complete resulting parts, day and correction facts,
checks preservation of prior movements, openings and request records, then adds
the owner audit and idempotency receipt to the same aggregate. After packing and
immutable archive-page staging, an optional correction-only callback rechecks the
native preview. Final authorization follows, then a synchronous cancellation and
deadline check immediately before CAS dispatch. A pre-dispatch failure leaves the
aggregate untouched; unreferenced immutable archive pages follow the existing
store contract. Once CAS is dispatched, its existing shield/join and unknown
outcome behavior remain unchanged.

A CAS conflict that changed the reviewed revision requires a new preview. The
service never automatically approves a refreshed value. Replaying the same
request returns its stored receipt before native acquisition or calculation;
reusing the request ID with different content is rejected.

The frontend retains the exact pending intent before submission. A hidden page,
unmount, offline event or owner/permission change aborts the active request and
suppresses late UI output. An uncertain result offers explicit receipt lookup or
an explicit resend of the same intent, never an automatic new approval.

Read endpoints:

- `GET /api/operational-balances/ad-spend-corrections/preview` with account ID,
  original date interval and expected session scope.
- `GET /api/operational-balances/ad-spend-corrections/receipts/{request_id}` with
  expected preview hash and session scope.

The saved receipt contains the exact before/after original obligations, source
digest, correction identities, revisions, reviewed differences and preservation
claims. It does not certify completeness of all native account history:
`source_completeness_verified` remains false in the preview.

## Verification

All added fixtures are synthetic and use dates in April 2030. Tests never import
the production server, access production credentials, or call an advertising
provider. Real database cases use a disposable loopback replica and ASGI HTTP;
one case exercises the complete existing source collector.

Local acceptance for this candidate:

- 60 pure/model correction cases plus 18 actual-Mongo/HTTP cases: 78 passed.
- The 18 actual-Mongo cases passed separately with only the Stock-style equal
  `OPERATIONAL_TEST_MONGO_URI` and `MZ2_TEST_MONGO_URI` capabilities, with both
  demand-test variables absent. No test skipped.
- 13 adjacent backend suites including those two, normal refresh lifetime,
  engine/wallet, recovery proofs, FX bank funding and reporting: 324 passed.
- Five frontend suites: 89 passed, including 13 correction UI cases.

Coverage includes all four native provider shapes, Snapchat SAR and pinned USD,
operational wallet-first bindings, approval/replay, no client amount authority,
same-native-cent SAR changes, hybrid split changes, incomplete/missing FX,
malformed retiring rows, source changes before approval/during reconciliation/
during packing/after staging, CAS conflict, owner revocation, cancellation and
deadline during final authorization, and observing a committed result after
disconnect. Manual movements and openings retain their exact contents.

For the focused new backend suites, use the repository's isolated Mongo fixture:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 PYTHONPATH=backend:backend/tests \
  python -B -m pytest --noconftest -p no:cacheprovider --asyncio-mode=auto \
  backend/tests/test_operational_balance_ad_corrections.py \
  backend/tests/test_operational_balance_ad_corrections_mongo.py -q --tb=short
```

Supply an explicitly owned loopback `DEMAND_READ_TEST_MONGO_URI`, an owned
`DEMAND_READ_TEST_MONGOD`, or the existing equal loopback Operational/Stock URI
capabilities. The fixture has no production fallback. Both test filenames match
the mandatory `test_operational_balance*.py` selection. Frontend tests use the
repository-pinned toolchain and `src/pages/operational/AdSpendCorrections.test.jsx`.

Local test timings establish acceptance, not production latency. Deployment and
native statement/receipt reconciliation remain separate release checks.
