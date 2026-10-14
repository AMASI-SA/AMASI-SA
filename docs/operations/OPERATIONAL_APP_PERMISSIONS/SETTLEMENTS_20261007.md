# Operational app settlements and assigned banks

User-approved scope: owner assigns one or more active MZ2 banks when granting
native movement entry. One bank becomes the default; multiple banks require an
explicit default. Reports remain a separate permission. No Production changes.

Stored in the existing owner/employee mobile access record as operational_banks.
The owner-only permission endpoint validates membership, type/readiness and the
default. Revoking entry clears this configuration. Native context returns the
assignment, native bank choices are filtered, and native movement submission
rechecks the assignment on entry and at persistence. Browser behavior is unchanged.
Previously granted staff must have banks assigned before their next native write.

Movement accepts optional ISO business_date; occurred_at remains the actual audit
timestamp. Omitted dates preserve existing request identity. The existing note
field carries the optional bank message. No receipt upload, writer, new financial
calculation, dependency, migration, backfill, release action or Production write.

Companion native change: three-column category/platform cards, settlements only
in this slice, incoming collection using existing allocation behavior, assigned
bank default, editable date and optional multiline bank message. Other category
cards are labeled for later work. Historical pending submissions retain retry UI.

Verification: 202 backend tests passed (all test_operational_balance*.py plus
test_employees_v2_management.py), isolated Mongo on localhost:27316 and disposable
databases. Owner management UI 26/26 passed. Native full yarn typecheck/verifier
chain passed, plus settlement model/date/render checks. Added tests cover invalid
banks, default selection, nonassigned API denial, live revocation, date retention,
concurrent retry, and changed-date idempotency conflict.

No new APK or device acceptance is claimed for this change. Earlier 970208d APK
UAT remains incomplete and does not validate the new cards. Next: paired Draft
review and isolated visual/device verification; employee card is a later slice.

Production writes = 0. #1263 and Production were not edited.
