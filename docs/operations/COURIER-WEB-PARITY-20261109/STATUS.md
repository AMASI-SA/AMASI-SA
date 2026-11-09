# Android76 merchant courier / web parity

Owner explicitly requested fixing and publishing the courier-only failure, then
clarified: use the same Mezan data path as web. This supersedes the earlier
requirement to repeat the Salla completed-status POST for merchant couriers.
External carriers retain that existing explicit-completion behavior.

Base: production branch hotfix/prod-snap-meta-final at
18c245748d22615332d10c674812d4362b88ae71 (PR1839).
Task branch: codex/courier-web-parity-20261109.

## Confirmed mismatch and change

Exact Android76 source 3f0567991c20c1b229811ceb8b852d8bb040bf6c sends its fetch
button to /carrier-label/v1/complete-and-refresh. Web and native print refresh
use /carrier-label/v1/refresh, whose merchant-courier path renders the current
Mezan order. The explicit mobile fetch previously used a separate provider
completion flow and full snapshot/lease checks. Successful browser printing
therefore did not exercise Android's fetch handler. The exact live rejection
condition was not traced; this change implements the owner's explicit parity
contract rather than claiming one unobserved error explains every device.

The existing Android URL now dispatches current Mezan merchant couriers to the
same v1 read handler used by web and native print refresh. No new label generator,
provider status POST, completion lease, APK, background job, polling, or cache
reset. Existing authorization, current courier/address selection, completion
and cancelled-order checks remain inside the shared handler. Existing optional
recipient-address recovery remains identical to web. External-carrier dispatch
is unchanged.

Changed files: backend/fulfillment_v2_routes.py and existing regression suite
backend/tests/test_amasi_shipping_compat.py. No frontend/mobile changes.

## Verification

Before source change, four new public-route checks: 2 failed (explicit courier
fetch incorrectly entered external completion), 2 passed. After change:
115 tests + 64 subtests PASS; 18 isolated-Mongo parametrizations SKIP locally.
Command: PYTHONPATH=backend:backend/tests PYTHONDONTWRITEBYTECODE=1
<isolated-venv>/bin/python -m pytest -q --asyncio-mode=auto
backend/tests/test_amasi_shipping_compat.py
backend/tests/test_mobile_assembly_refresh_actions.py
backend/tests/test_manual_courier_label_refresh_race.py
backend/tests/test_unified_completed_carrier_label.py
backend/tests/test_courier_label_recovery.py
backend/tests/test_store_courier_unified_print.py --disable-warnings --tb=short
Exit 0. Includes 30 sequential Android URL requests exactly matching the shared
web result, rejecting every provider call, ignoring an old unrelated manual
lease, preserving status verification time and not confirming physical print.
Disabled staff, incomplete/cancelled orders, external dispatch covered.
Diff whitespace check PASS. Real Mongo CI and production verification pending.

## Release state and next safe steps

Source candidate only. Production changed by this follow-up: NO. No new lease,
merge or publication; user already authorizes all needed release actions.
Shared Code Server now displays login after browser session reset. Do not alter
/app before authentication, fresh guard status (require inactive) and HEAD.
Previous1839 UI success/verification was observed in prior session; the durable
ledger missed final acceptance, so confirm its saved verification before release.
Create PR, require exact source CI and governed v5 A/B intent, shared rehearsal,
normal merge, prepare/prepublish, publish once and three official verify probes.
No real business transactions have been used in this candidate's tests.
Physical Android printer acceptance remains unavailable in this environment.
