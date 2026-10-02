# Final acceptance evidence — no feature work

Candidate is frozen at HEAD `88cc9131027fd6783a46e2e00fc6aaec4788b8fa`,
TREE `57483f44efa381ca872262f5a8bc61d2a87cae93`, Production/base/rollback
`83363097d48e034dc7140a60c290efc684e1ffde`; PR1240 remains OPEN/Draft.
Source A is38586718; reviewed Intent B is88cc9131.

This **separate evidence branch** is not a replacement release candidate and
must not be merged into the frozen A/B source. It preserves acceptance records
without invalidating the reviewed source/intent pair. Executions target the
clean candidate checkout, not this evidence checkout.

User authorizes FINAL ACCEPTANCE only: existing contracts, harnesses and checks.
No application, test assertion, configuration, economic contract or writer edit.
No Production mutation, merge, deploy, opening, activation, control toggle,
financial schedules, lease or publish. Synthetic isolated regression remains
distinct from actual business/physical acceptance.

## Contract findings before execution

The existing16-stage harness explicitly proves setup only. Physical inventory
approval has an existing G47 contract but needs exact count/cost/location source,
owner approval and verified opening/safe_active; a synthetic run cannot attest
actual stock. Opening and transition contracts exist and have real-Mongo tests,
but a combined final business scenario/accepting actor is not defined in the
setup harness. The user has been asked for the authoritative inventory/UAT
source and whether positive Opening/Activation acceptance is isolated execution
or denial-only. Independent permitted work continues.

Smoke B's explicitly approved Acceptance contract is reusable as-is: actual
full-app startup, real password/MFA, initiallypaused owner, missing-session404,
opening-draft423, full database fingerprints and source/runtime identity proof.
No non-loopback request is permitted. PASS remains Acceptance-only, never
production_verified or Production readiness.

## Verification matrix

| # | Gate | Planned evidence | Initial state |
|---|---|---|---|
| 1 | Full Business UAT | Agreed combined business scenarios/actor, actual execution | Contract/data clarification pending; setup insufficient |
| 2 | Physical-stock approval | Authoritative count/cost/location and approved G47 flow | Actual source/approver missing |
| 3 | Opening acceptance | Existing original-source/review/post/replay/rollback contract | Positive acceptance interpretation pending; regression available |
| 4 | Activation acceptance | Existing transition/gate contract, same owner and prerequisites | Positive acceptance interpretation pending; live forbidden |
| 5 | Smoke B | Existing full-app Acceptance harness on exact88cc | Prepared for execution |
| 6 | Backend regression | Existing147-file runner on exact88cc, all assertions | Prepared for execution |
| 7 | Frontend regression | All existing suites on exact88cc | Prepared for execution |
| 8 | Build | Ordinary exact88cc compile plus governed B CI/rehearsal | Prior exactB PASS, compile refresh planned |
| 9 | Real Mongo | Owned Mongo8 replica and standalone, UUID data, cleanup | Fresh isolated instance planned |
| 10 | Supplier invoice | Existing native invoice/settlement suites, per-case output | Included in backend |
| 11 | Employee/payroll | Existing accrual/payment/report contracts | Included in backend |
| 12 | Shipping/COD/POS | Existing TrackF/manual POS/late proof/cash contracts | Included in backend |
| 13 | Advertising | Existing TrackE native/bridge/automation contracts | Included in backend |
| 14 | Bank transfer | Existing native source/identity/payment contracts | Included in backend |
| 15 | Advances/refunds | Existing ownership, amounts, replay and rollback | Included in backend |
| 16 | Payment providers | Existing statements/recognition/settlement contracts | Included in backend |
| 17 | Daily movements | Existing native classification/transfer/expense/supplier paths | Included in backend |
| 18 | Security Gate | ExactB GitHub job evidence | Prior39/39 read back; final refresh when appropriate |
| 19 | CodeQL | ExactB GitHub job evidence | Same |
| 20 | G47 | Existing real Mongo and CI contracts | Included in backend and CI |
| 21 | Native SSOT | Source boundaries, monitored execution, hashes and guards | Prior scoped PASS, refresh planned |
| 22 | Release Readiness | All independent business and technical gates | NO; not inferred from CI |

Raw execution artifacts will be copied with a SHA256 manifest. Do not promote a
failed/partial step or historical source result. Each checkpoint records exact
candidate identity separately from this evidence branch's commit.
