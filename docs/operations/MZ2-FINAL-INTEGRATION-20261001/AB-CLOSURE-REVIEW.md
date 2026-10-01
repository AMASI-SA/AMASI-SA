# A/B closure on the reviewed Final Integration source

The user-requested review starts at PR #1232 HEAD
`db6bd9c6b8941e758d58407749b60ed6aff22f5e`, TREE
`3229f42b67739dbc6b10dc5f83ea4028838b73d3`. Production/base J remains
`901568ccaaf510dc1f84d9c28f38d368d07dc64d`.

## Concrete B defect and delivered repair

A fresh real-Mongo reproduction against that exact baseline proves that old
sealed iMile evidence can post a new courier fee after the operational carrier
changes to Store Driver. The test fails specifically `DID NOT RAISE
HTTPException`; it is not an environment failure (1 failed, 2.19 seconds).

Reuse the existing, separately delivered adapter from PR #1234
`f2ee9e7daa397588faa1b70b08409027bdba7ec2` and its necessary operational
producer delta from PR #1231 `6507c813a73ea71a5eab2a8d39c5bb92a4075c70`.
The nine operational runtime files correct existing order/sync/webhook/label
identity persistence; they create no new operational API. Only their delta is
applied, preserving the native financial source snapshot methods. The four
associated operational tests and eight accounting source/test files are reused.
The unrelated frontend polling/UI changes and separate-PR composition harness
are excluded. The existing Track F CI selection adds the new regression cases;
the existing G47 glob already includes the new real-Mongo operational suite.

The read-only guard checks owner, exact current configured carrier, active
outbound shipment ID and AWB against sealed evidence before a new fee. Missing,
stale, ambiguous or Store Driver evidence fails closed. Posted replay runs first
and stays immutable. The existing `accrue_fee -> _post -> post_journal_v2`
writer, tariff calculation and atomic owner boundary are reused. COD, revenue,
driver fees, manual POS, bank settlement, write-control and cutover are unchanged.
No history repair, resealing or inferred identity is authorized.

Focused combined-source result: **159 passed, 42.77 seconds**, seven suites,
including the reproduced defect, native fee concurrency, current-carrier
transaction race, operational pause and standalone rollback. Full regression,
exact-source CI and fresh scoped SSOT audit must finish before final delivery.
Prior CI is historical evidence, not acceptance of this new source.

## Frozen release boundary

PR #1232 remains unchanged. The successor source carrier starts from its exact
source A `1cb6845d4b0246fe88da3b10100477d92af54314`; every prior source byte is
retained except the declared integration delta. This excludes only the old
intent-only B, as required by Release Guard v5's no-intent-in-J-to-A history.
After the new source is tested/built, a new intent-only B must be frozen. No
guard is relaxed and no old build proof is reused. Exact final HEAD/TREE/results
are recorded externally in Issue #1006 and the successor review PR.

## C / acceptance holds — unchanged

| Item | Status | Proven boundary |
| --- | --- | --- |
| rich_shipping_approval | BLOCKED | Rich approval gate 423 and evidence authority 503; no lossless native consumer |
| complete_h2_review_history | BLOCKED | Pending reader exists; complete accountant history read contract absent |
| native_physical_cash_reconciliation | BLOCKED | Operational cash is explicitly pending native balance linkage |
| smoke_b_proof | BLOCKED | Environment/owner holds and missing proof consumer remain |
| full_16_stage_business_uat | BLOCKED | Isolated assertions do not accept rich shipping, physical approval or live Stage 16 |

Manual accountant POS remains A, delivered and retained; it does not require an
external processor source. No C implementation is included. Release Ready=NO.

Production financial writes=0. Production merge/Deploy/Opening Post/Activation=NO.
Write-control unchanged; no financial scheduler or Release Guard lease started.
