# Print snapshot recovery

Owner authorized fix and production publication, with Ready rules unchanged.
Base: b4e5d7a39 (production runtime source 0f458d1fd, independently read from health).

Read-only incident evidence: assembly completed, one ready piece of one;
canonical completed; source revision7/pendingtrue, lifecycle revision6,
acceptedtrue/retryfalse/legacy/no plan. Raw provider update clock was ten seconds
ahead of watermark/lifecycle. No customer information retained here.

Root paths: full resync/batch upsert bypassed component watermark; light status
page replaced the full raw clock. Print rejected pending evidence before any
fresh authoritative recovery. Fix fences full sync, separates light clock,
rejects older snapshots against all observed clocks, and refreshes pending print
evidence with autofulfillment, financial observation and billing disabled.
Only the existing unchanged-contract validator can carry acceptance forward.
All original assembly and final provider print checks remain.

Verification in progress: initial sync6 real Mongo regressions pass; recovery4
pass; prior affected unittest14 pass. Independent review identified stale
resync must report okfalse for financial preflight, and a newer light cancellation
must fence recovery; both addressed with regression coverage in progress.
Broader local pytest and required CI still pending. No production changes.

Next: complete independent review and fresh tests, commit source A, push task PR,
required CI and governed A/B artifact build, freeze intent-only B. Then clean
/app adapter rehearsal, guard status/prepare/prepublish, one publication and
three-probe verify. Never reuse another lease. After verification retry printing
the affected order through the normal UI; no manual flags or stock writes.
