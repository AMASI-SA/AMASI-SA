# 409 semantics resolved — acceptance subsets pass, final gate held

No demonstrated candidate defect; **NO CODE CHANGES to PR1240**. Candidate
HEAD88cc9131027fd6783a46e2e00fc6aaec4788b8fa,
TREE57483f44efa381ca872262f5a8bc61d2a87cae93.
Production/base83363097d48e034dc7140a60c290efc684e1ffde; PR OPEN/Draft.
Only this separate evidence branch changed. Source A and intent B are unchanged.

## Exact409 semantics

Actual authenticated requests through the unchanged assembled public router,
existing JWT verifier and fresh persisted grants returned:

| Public path (prefix `/api/financial-provider-apps/accounting-module`) | Result |
|---|---|
|`/financial-accounts/opening-balances/drafts/synthetic/post`|409 `opening_onboarding_required`, live_actions_enabled=false|
|`/financial-accounts/transition`, target=v2_active|409 `onboarding_activation_locked`|

[Responses](../evidence/gate409/probe/public-409-observed.json) and
[whole DB/control before/after](../evidence/gate409/probe/public-409-probe.json).
No route remount or positive post was attempted. The draft ID is synthetic
because quarantine precedes any draft read. The probe used its own initially
unpaused fixture owner; no control was toggled.

This is **Case A: expected fail-closed**, not a failed valid financial operation.
The limitation is stronger than waiting for authorization: public routes are
unconditionally quarantined after authentication/permission/pause guards.
Production verification or authorization alone does not change their routing.
A paused owner may correctly encounter423 first.

Normative sources on immutable88cc:

- `backend/accounting_financial_accounts.py:1400–1424`: quarantine and returned
  internal engines without public positive registration.
- `docs/operations/MZ2-TRACK-G-20260930/track-g-old-routes.md:22–39`: intentionally
  retains post/reverse/transition for regression and a future separately
  authorized live gate. Onboarding consumes only create/preview/review.
- `backend/accounting_onboarding.py:237–254` and Track A `API_CONTRACT.md:91–94`:
  separate source/live readiness; no passed-Smoke consumer or positive final
  gate. Connected native writers still report Production verification required.
- `backend/tests/test_financial_accounts_real_mongo.py:117–127`: engine remounting
  is explicitly regression-only; success cannot prove public reachability.

Release Guard is a separate deployment provenance/lease gate (AGENTS protocolv5
and `scripts/production_release_guard.py`), not the emitter of these accounting
HTTP409s. Build/CI/provenance success does not authorize a journal or enable the
routes. No lease/prepare/prepublish/publish/verify or Production request was run.

## Executed without Production

1. Unchanged C5 browser actual UI/HTTP workflow **16/16 PASS** on88cc.24 independent
   expected legs balance4929 each. Planned inventory295; Stage16 lock and separate
   paused-owner404/423 preserved. Raw responses/timestamps/source/screenshots/
   cleanup in `../evidence/gate409/setup`. No setup journal/draft/stock init.
2. Two actual public409 requests PASS expected refusal, whole DB/control
   fingerprints identical before/after; source manifests identical.
3. Existing G47 API cases **4/4 PASS**: original-byte import, actual approval,
   concurrent idempotent replay, atomic rollback/retry. Eleven HTTP responses
   retained, including four successful approvals across scenarios. The existing
   declared prerequisite is a synthetic verified opening, not a user journey
   through public Opening. Original assertions/routes unchanged. Approval creates
   five receipts/four cost states/quantity10/value60 and no additional journal;
   the fixture itself creates one native opening as prerequisite. Do not call
   all test writes zero.
4. Existing exact88cc Smoke B remains Acceptance-only PASS. Existing CI readback
   is39/39 success. No new Smoke/whole-regression/CI run is claimed for these
   documentation changes; previous successful exact-source evidence is retained.

Both environments used fresh loopback Mongo8.0.12 replicas on D and sanitized
inherited environments. The setup server/browser and API observer denied
external connections. All fixture databases removed; owned processes stopped;
ports27137/18773 closed; data retained offline. Independently rehashed2885 source
files, no candidate change.

First external orchestration ended INCOMPLETE **after successful16/16 browser**:
its new probe address omitted `/financial-provider-apps`. Response status was
not persisted, so none is fabricated here. The corrected address follows the
existing Track G contract; expected409 assertion unchanged. Remaining checks
ran in a new fixture (`--probe-only`); successful browser evidence was preserved.
Original attempt retained under `setup`; initial runner at evidence commit87e8ea3d.
This was an evidence-runner defect, not a demonstrated PR1240 defect.

## Physical stock

The existing physical-inventory API is executable in Acceptance and now has
fresh positive evidence. It cannot complete from this wizard session: no posted
opening/safe_active exists, and planned metadata is not counted stock.
Actual business stock approval still needs authoritative count/cost/location/
barcode evidence, owner sign-off, exact verified active opening and per-account
value match (G47 OPENING-INVENTORY.md:10–12,63–75; P07:12–20,80–87).
No supplied Production stock facts or actual physical approval are claimed.
The separate fixture does not alter onboarding physical-approval readiness.

## Remaining dependencies

| Class | Remaining requirement | Current result |
|---|---|---|
|Acceptance executable now|Setup16/16, public409/paused423, declared-fixture physical API, Acceptance Smoke|Scoped evidence complete|
|Authorization/data decision only|Owner cutover/source/count sign-off, accepting actor, explicit live execution authority|Cannot be inferred from tests; does not unlock current routes alone|
|Positive public gate availability|Separately reviewed live-gate contract/wiring for deliberately withheld public Opening/Activation, reusing existing engines|No delivered positive path in frozen source; no implementation here|
|Production verification|Exact deployed SHA/runtime/artifact and any required trusted live proof under separate authority|Local Acceptance/CI cannot attest it; no deployment/Production probe|
|Actual financial/stock execution|P07 verified opening/12of12; real stock initialization; P08 activation, operational scenarios, reconciliation/stabilization|Requires separately authorized effects, not merely a signature|

P07 completion includes a **posted verified** opening (`PHASE-07-OPENING-BALANCES.md:126–149,210–223`).
P08 starts after P07/safe_active and verified Production SHA, then requires
operational event/reconciliation/stabilization evidence (`PHASE-08-ACTIVATION-VERIFICATION.md:12–20,36–45,147–170`).
The setup contract excludes these actions. No reviewed contract permits marking
Full Business UAT complete by replacing them with setup+expected409. Refusal
tests are successful guard tests, not completed positive acceptance.

**Full Business UAT NOT PASS; Release Readiness NO; Release Candidate READY
cannot be claimed under the requested all-gates criteria.** No financial feature,
writer, default, fallback, control flag or accounting semantic is changed.
Next is a separately reviewed acceptance/live-gate decision, not automatic
guard changes or Production mutation.

Production financial writes by task0; write-control UNCHANGED. Merge, Deploy,
Production Opening/Activation, schedules, release lease/publish NO.
