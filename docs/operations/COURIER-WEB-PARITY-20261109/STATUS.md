# Android76 courier web parity — PR1840

## Current state
Implementation and exact frozen-head CI are ready. NOT merged or published.
Production changed by this follow-up: NO. No release lease was created.
The owner explicitly authorizes fix, merge and publication, and clarifies:
Android courier labels must use the same Mezan data path as web.
This overrides the previous repeat-Salla-completion requirement for merchant
couriers only; external carrier behavior stays unchanged.

Repository: AMASI-SA/AMASI-SA
PR: https://github.com/AMASI-SA/AMASI-SA/pull/1840
Task branch: codex/courier-web-parity-20261109
Source A: a914bfee26f2071c9efbf883af26e40a4b1c6f4a
Source tree: 044bfbae2c111544b2d0045ee444cba025adc340
Intent-only B: 8f8cd46ef30cb440692aaa76df6c7bc17d55e9aa
Deployment candidate tree: d9a32baed4d34bec87eb6b652da79466644220e5
Production base: 18c245748d22615332d10c674812d4362b88ae71
Release: rg5-4ec52c870c627354a6ef8762d99db5ccdd1382e15e210658c14990e6a5d3f2a8

This STATUS lives on a separate evidence-only branch. Do not deploy that branch,
merge it into the frozen candidate, or rewrite A/B.

## Confirmed mismatch and implementation
Exact Android76 source 3f0567991c20c1b229811ceb8b852d8bb040bf6c sends its
fetch button to /carrier-label/v1/complete-and-refresh. Successful web printing
uses the existing Mezan document handler through /carrier-label/v1/refresh.
The Android fetch URL now detects the current merchant courier with
mezan_courier_name and dispatches to that same handler, issue=False.

The only production source change is backend/fulfillment_v2_routes.py (9 lines).
Tests are added to backend/tests/test_amasi_shipping_compat.py.
The shared handler keeps authorization, current courier/recipient selection,
assembly completion and cancellation checks. No provider status POST or manual
completion lease for this courier flow. Existing optional missing-recipient
recovery remains identical to web; do not claim every incomplete-address request
is provider-free. External carriers keep the original explicit-completion flow.
No APK, frontend, worker, polling, accounting or ad changes.

Live browser success before this change did not exercise Android initial fetch.
The exact live native rejection was not traced; do not claim an unobserved
single error explains all physical device failures. No physical printer
acceptance or live business transaction is claimed.

## Verification
Before production change, four new public-route tests: 2 expected failures,
2 passes. Final local suites: 115 passed + 64 subtests, 18 real-Mongo cases
skipped locally, exit0. Thirty sequential authenticated Android fetch requests
equal the web response, with provider calls forbidden and an unrelated stale
manual lease present. Completion/cancelled/disabled-staff guards and external
carrier dispatch also covered. git diff --check PASS.

Local command:
PYTHONPATH=backend:backend/tests PYTHONDONTWRITEBYTECODE=1 <venv>/bin/python
-m pytest -q --asyncio-mode=auto backend/tests/test_amasi_shipping_compat.py
backend/tests/test_mobile_assembly_refresh_actions.py
backend/tests/test_manual_courier_label_refresh_race.py
backend/tests/test_unified_completed_carrier_label.py
backend/tests/test_courier_label_recovery.py
backend/tests/test_store_courier_unified_print.py --disable-warnings --tb=short

Exact B: all 13 applicable workflows PASS; unrelated conditional TikTok skipped.
Source review-completion run was superseded/cancelled by B; B complete PASS.
G47 42630329029: 327 tests +127 subtests; 436 additional cost tests PASS.
Review42630329241: 251 tests+59subtests, 658+345subtests, final1 PASS;
executed-case validation rejects skipped/failing cases.
Readiness42630328952: Node20/24 dispatchers and gitless clean-clone adapter,
isolated package and HTTP verification PASS, exact rg5 identity above.
Shipping42630329064 and other exact B workflows PASS.

Source readiness42629430433 produced artifact13680362180. ZIP SHA256:
530da234247770724eb8f3e8b0f968633f27452f8cddf288b75aa08354590f04.
Candidate validator EXIT0 after fetching necessary ancestry. Intent Git blob:
a4b73872097f494b8adbf02f5a42cfb40d79c32a.
B diff after A is ONLY release/release-intent-v5.json.
No production source was altered after freeze.

## Production and authentication evidence
Code Server secure login succeeded. Fresh /app guard active:false, HEAD
18c245748d22615332d10c674812d4362b88ae71, tracked tree clean.
Old /tmp/courier1839-verify-final.json was lost after environment/session reset.
Fresh public health confirms published1839 source
f8af8c2027f1512aa5377933e5229085f375451e and
rg5-e80fcfae723c5e865a69dba84df03e1ecdff7faaae323e464d5cc93aee3a420b,
with verified identity, critical hashes and frontend verified all true.
Do not repeat1839 publication or manufacture a retained final verify file.

Emergent management is a separate expired session. Owner chose email sign-in,
then secure credential request returned declined. No retry or alternate login
was attempted. This blocks UI publication, not source readiness.
Shared /app was not changed by this task.

## Exact next safe action
When the owner resumes management authentication, re-read AGENTS and Issue1006,
check current PR1840 exact B and production ancestry; read fresh /app guard and
HEAD before mutation. Require inactive; preserve unrelated files and leases.
Normal merge A/B (no squash/rebase), fast-forward clean shared workspace,
run governed v5 adapter and require exact reviewed identity. Only then owned
prepare/prepublish immediately before one UI Publish action. Require newer
explicit deployment success, official guard verify three consecutive probes,
and fresh active:false. Save final verification durably and update this evidence
branch, PR1840 and Issue1006. Existing publication authorization persists.
Android76 can consume the fix without reinstalling an APK once published.
