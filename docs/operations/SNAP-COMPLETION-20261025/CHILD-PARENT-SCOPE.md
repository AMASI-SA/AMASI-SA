# Shared Snapchat child reporting scope

The live owner provenance diagnostic identified complete campaign DAY facts in
the SAR account while both child levels had zero requests. The shared producer
selected child parents only from positive HOUR activity. This also affected any
other account whose DAY report contained parents absent from that HOUR subset.

The producer now passes exact successfully parsed and persisted DAY campaign
identities to both child requests, together with the existing HOUR parents.
Returned zero-metric DAY parents remain in scope. Native account timezone,
account ownership, case-sensitive IDs, requested dates and request budgets stay
bound. Catalog membership does not manufacture financial facts or zero coverage.
Failed or unproven DAY publication cannot hand off new parent scope. Nonempty
date scope with no child requests remains explicitly unknown/incomplete.

USD/Los Angeles and SAR/Riyadh use the same implementation. No currency branch,
Saudi reader clone, advertising mutation or budget change is added.

Root verification used PyMongo4.6.3, Motor3.3.1 and mongomock4.3.0 with synthetic
provider responses. On the old producer, six regressions failed and one passed
in0.57s: no child requests and false complete/no-data claims were reproduced.
The corrected seven cases passed in0.63s. The affected reporting/period suite
passed164 tests with one environmental skip in5.25s. This is emulated-storage
evidence; mandatory repository CI and verified production acceptance remain
required.

After a governed deployment is verified, recover only the affected owner's
selected account and bounded closed dates through the normal sync route, then
reread producing-run provenance and actual decision readiness. A successful
source sync alone does not grant decision or spending authority.
