# Approved native customer returns/exchanges access

The existing movement-write and reports-read grants are reused independently.
GET full case lists requires reports. Exact-order entry and shipping quote input
lookups, create, return confirmation and exchange actions require movement-write.
Write-only entry and mutation responses exclude summaries, contribution and
supplier invoice histories, and refund financial details. Per-CAS authorization
rechecks grants, owner/actor and the assigned bank for actual bank refunds and
exchange contributions. Core financial services and Web screens are unchanged.

Fresh real local Mongo/ASGI: 254 backend tests PASS, no failures/skips (operational
suites and employee management). 11 new tests cover both workflows across all
four grant combinations, concurrent retries, minimal write-only responses,
supplier invoice action authorization, bank rejection and permission revocation.
Two earlier assertions that all native case mutations were forbidden were
updated because the user explicitly expanded the authorized scope. A 409 from
reusing a failed actual-refund request ID for a different pending payload was
correctly rejected; the test now uses a distinct command ID.

Affected Web tests: 61 PASS, six suites. Native full typecheck/verifier chain PASS
with 14 new case tests. New exact-candidate APK/device UAT remains pending; no
synthetic tests are represented as phone acceptance. Native inventory custody,
supplier discounts/physical return UI and certain case continuation controls
remain incomplete, documented in the native companion checkpoint.

No Accounting writer, opening or Audit grant, migration or Production action.
Only companion Draft #1271 changed; frozen #1263 is untouched. Production writes=0.
