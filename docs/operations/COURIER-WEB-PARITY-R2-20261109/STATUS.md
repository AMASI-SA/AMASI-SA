# Android courier web parity R2 — published and verified

## Current outcome
COMPLETE. Production changed: YES. PR1843 merged and published once on
2026-11-09. New green UI Deployment finished at18:18Riyadh, versionbcb03ed,
deployment141ba48c-7c31-5c32-a617-2adc2dd5b424.
Official production_release_guard.py verify --url https://mezansalla.com EXIT0:
verified=true, checks=3, exact intended source/release/deployment identity.
Fresh guard status active=false. No publication action remains.
See production-verification-summary.json for the observed acceptance values.

## Frozen source and deployment
Repository AMASI-SA/AMASI-SA; production hotfix/prod-snap-meta-final.
Task branch codex/courier-web-parity-r2-20261109.
Source A a057b0357fa1370d1735d7ab93a9e06113374516.
A tree c009c02b3cc1853095c48f684e90d1d65f09fd2a.
Intent-only B6f2dc1bd382ead3670d26f796b4662b5bcac41b9.
B tree32306e7aa9074c6d639eec0ec8a57654ebdf2430.
Normal merge9b98bc1817ffce7d644f408c63e78bedf915686f, tree exactly equals B.
Basecedd7158ba1a7fbae13d1503a503c61b19ad7ae6 retains payment-display1842.
Runtime rg5-4a8fc0d5b6527684bb4fdc4049adbf7e8dd7d15c0744206bca9ebf1229ba7776.
Owner actor codex-courier-web-parity-1843; lease successfully closed by verifier.
This evidence branch is NEVER a deployment input.

## Shipped behavior
backend/fulfillment_v2_routes.py dispatches Android76 complete-and-refresh for
current merchant couriers to _mobile_carrier_label_v1(issue=False), exactly the
web/print-refresh handler and current Mezan data. This avoids the Salla status
POST and manual completion lease for this courier fetch.
Existing permission, current carrier/recipient, assembly completion and
cancellation checks remain. Optional missing-recipient recovery is still the
same as web; not every incomplete-address request is asserted provider-free.
External carrier completion flow unchanged. No new APK is required for Android76.
No frontend, worker, polling, accounting or advertising changes in this fix.

## Verification
Existing backend/tests/test_amasi_shipping_compat.py adds authenticated HTTP
coverage for30 consecutive Android fetches equal to web with provider calls
forbidden and a stale unrelated manual lease present; employee permission,
incomplete/cancelled and SMSA external dispatch covered.
Original baseline2expected failures/2passes. Final local six suites115PASS,
64subtestsPASS and18explicit local Mongo SKIP, exit0 in6.01s. These local skips
are not acceptance; exact-B CI ran the required isolated database coverage.
All13 applicable exact-B workflows PASS; unrelated conditional TikTok skipped.
Review42637866581/job129230932735:251+658+1cases,404subtests PASS, executed-case
no-fail/no-skip guard PASS. G47 run42637866790 both jobs PASS.
Readiness42637866784 Node20/24 gitless clean-clone/package/HTTP adapter PASS.
Source candidate42637408685/artifact13684007040 ZIP SHA256
7c281c713dd84cb7e90ab6fc939dd19895cfb8717b874b931fd0120f1a7bf552.
Local exact source/base/manifests validator EXIT0.
Shared adapter initial stop: untracked backend/__pycache__; preserved intact in
/app/.release-preserved/courier1843-bytecode/. Source remained unchanged.
PYTHONDONTWRITEBYTECODE=1 REACT_APP_BACKEND_URL=https://mezansalla.com yarn build
retry EXIT0 in64.77s, exact reviewed identity. Fresh ownedprepare/prepublish
EXIT0; one Publish changes click at18:09Riyadh.
Official3probes verify backend identity/hashes and canonical/cache-busted
frontend artifacts/build-meta JSON, then release matching lease. Public health
independently confirmed ok, intendedA/release, verifiedidentity/criticalhashes/
frontendbuild alltrue. Boot2026-11-09T15:18:20.055913+00:00.

## Scope, limitations and next safe action
No physical Android/printer acceptance or live business mutation was performed
for verification; automated HTTP parity and publication identity are proven.
No claim that this explains every earlier native freeze symptom.
No remaining merge/publish step; next is ordinary phone use/physical print
acceptance. Do not republish this release when resuming.
Prior1840 frozenA/B preserved, closed superseded after1842 advanced during
management login. Prior1842 release owner finished before any courier shared
mutation. Unrelated finance1844/1846/1847 and earlier1825 are not merged here.
No full actual Cloud Build argv transcript was exposed by UI; do not claim one.
Historical checkpoints remain in this evidence branch's earlier commits.
