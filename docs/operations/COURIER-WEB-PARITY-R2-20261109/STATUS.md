# Android courier web parity R2

Owner authorizes fix, merge and publication. Android merchant-courier fetch must
use exactly the web path and current Mezan data. External carriers unchanged.
No new APK is needed for Android76.

## Continuation and base
Prior frozen PR1840 A a914bfee26f2071c9efbf883af26e40a4b1c6f4a /
B 8f8cd46ef30cb440692aaa76df6c7bc17d55e9aa is preserved unchanged and must not
be deployed: production advanced during management sign-in.
PR1842 merged as cedd7158ba1a7fbae13d1503a503c61b19ad7ae6 and is currently
publishing under actor codex-payment-display-1842. Fresh shared guard ACTIVE;
no /app mutation or new lease is permitted until its owner finishes.
Management login succeeded; user resumed in this conversation.

R2 branch: codex/courier-web-parity-r2-20261109
R2 source base: cedd7158ba1a7fbae13d1503a503c61b19ad7ae6
Only the previously reviewed production-route and regression test changes are
transplanted to this base. Base release intent is retained untouched at source A.
PR1842 payment-display behavior and all unrelated source are preserved.

## Change and validation
backend/fulfillment_v2_routes.py routes Android76 complete-and-refresh requests
for current merchant couriers to the same _mobile_carrier_label_v1 read handler
used by web, issue=False. No Salla status POST or manual completion lease.
The existing permission, current carrier/recipient, assembly completion and
cancellation checks remain. Optional missing-recipient recovery stays identical
to web; not every incomplete-address request is asserted provider-free.
External carrier completion flow remains unchanged.

Tests in backend/tests/test_amasi_shipping_compat.py cover 30 sequential Android
HTTP requests equal to web with provider calls forbidden and a stale unrelated
manual lease present; disabled employee, incomplete/cancelled order and SMSA
external dispatch are covered. Original baseline had 2 expected failures/2 passes.
R2 after new production base: exit0, 115 tests+64subtests PASS,18 local Mongo SKIP
in6.01s. Same six suites:
test_amasi_shipping_compat, test_mobile_assembly_refresh_actions,
test_manual_courier_label_refresh_race, test_unified_completed_carrier_label,
test_courier_label_recovery, test_store_courier_unified_print.
Command: PYTHONPATH=backend:backend/tests PYTHONDONTWRITEBYTECODE=1 <venv>/bin/python
-m pytest -q --asyncio-mode=auto <six backend/tests/*.py paths>
--disable-warnings --tb=short.
Prior exact B all13 applicable workflows and gitless Node20/24 rehearsal passed;
R2 requires its own CI/intent because the source base changed.
No physical Android/printer acceptance, live business write or incident-wide
root-cause claim.

## Current state / next
R2 source only; no merge, new lease or publication. Production changed by this
task: NO. Create new PR replacing1840, run R2 CI/readiness, freeze new intent-only
B after artifact validation, require exact B checks. Preserve frozen1840.
Wait for1842 owner to close its lease. Recheck production branch and guard before
any shared mutation. Then normal merge, shared governed rehearsal, owned
prepare/prepublish, publish once, new explicit success and official3probes.
Update Issue1006 and evidence-only STATUS; never deploy evidence branches.

## Frozen release checkpoint
New source A a057b0357fa1370d1735d7ab93a9e06113374516; intent-only B
6f2dc1bd382ead3670d26f796b4662b5bcac41b9/tree32306e7aa9074c6d639eec0ec8a57654ebdf2430.
Readiness42637408685 PASS; candidate artifact13684007040 SHA256
7c281c713dd84cb7e90ab6fc939dd19895cfb8717b874b931fd0120f1a7bf552.
Local exact A/base/manifests candidate validator EXIT0. Runtime
rg5-4a8fc0d5b6527684bb4fdc4049adbf7e8dd7d15c0744206bca9ebf1229ba7776.
PR1843 B CI pending. UI prior1842 finished17:42Riyadh/version7df3fa1, but its
owner's official guard verification/lease closure remains pending. Courier
read-only status waits on the operation lock; do not start another verifier
or change shared source. No courier merge, lease or publication yet.
This is an evidence-only continuation document, never a deployment input.

## CI and merge completed
All13 applicable exact B workflows PASS; unrelated TikTok conditional skipped.
Final review251+658+1 tests /404subtests PASS, executed-case guard PASS.
Previous1842 owner finished: fresh shared guard active:false. Normal merge
9b98bc1817ffce7d644f408c63e78bedf915686f preserves A/B. Shared fast-forward and
governed build started only after fresh inactive guard and clean tracked tree.
Log /tmp/courier1843-adapter.log; no new lease or UI publication yet.
Next read adapter completion, require exact intent, prepare/prepublish actor
codex-courier-web-parity-1843, publish once and verify3/statusfalse.
