# Explicit local account choices and manual owner selector

Direct parent7e686 UI and its699 owner route/helper remain frozen. Root approved
new files only. Assessment2c2516 rejected the existing overview as an ownership
list: it caps50 and fills missing account evidence from provider defaults.

Unattached fixed GET `/google_ads/merchant-products/choices` accepts no caller
owner/account/default IDs. Actual current-user dependency and existing principal
must identify an owner; exact auth-relevant dependency fields bind the initial
local owner snapshot, including password timestamp and absent/null/BSON types.

Two bounded PRIMARY/snapshot read-only transactions read one unique owner,
exactly the Ads and Merchant provider documents, and the complete scoped account
set with400+1 sentinel. No account-state filter hides malformed/stale rows.
Missing/duplicate/invalid data refuses. Every provider/account must explicitly
be connected/api_connection in this conservative slice. Empty local sets are
valid; no default is created. Account identity aliases must agree. IDs remain
canonical strings; optional bounded display names are inert text and absent
names fall back only to the same ID. New projection adds display_name to the
frozen account projection; all other encoding/ID/projection helpers are reused.

Both private signatures must match before returning names/IDs. Replacements,
permission revisions, timestamp changes, absent/null and BSON type changes
invalidate the result. Snapshot reads are5s driver/6s coroutine bounded; total
route15s.256KiB is a post-materialization signature-size limit, not peak DB memory.
No automatic application retry or nontransactional fallback. No credential
collection, native Google read, settings, account selection or connection write.

Public source is mezan_google_local_membership. Response includes authenticated
owner ID for UI scope matching, opaque observation ID, observation time,
matching_complete_bracketing_snapshots, local count and separate Ads/Merchant
candidate arrays. All remote completeness/native link/Merchant access/continuous
authorization/mapping/Ads eligibility/execution flags remain false; provider
calls and writes are zero. No private owner snapshot or credential data returns.
This is complete local enumeration at matching snapshots, not complete remote
discovery, uninterrupted authorization or proof any chosen pair works.

The new wrapper uses actual AuthContext and only renders in authenticated owner
mode. Both selections start empty, even for a single candidate; no URL,
workspace default, first-account or browser-storage adoption. Only a manual list
button sends the fixed existing-api GET, finite timeout, signal and existing
no-refresh/replay marker. Refresh clears old choices/evidence before reading;
auth/owner change unmounts state; late/cancelled responses cannot restore it.
Explicit selections cause no request. The unchanged child performs the separate
manual observation request and the699 server route re-proves credentials,
raw local membership and native link sets. Parent/router attachment remains root.

The successor implements both new frontend files and tests. The backend fixture
is generated through the actual local HTTP route; a second fixture is generated
through the actual699 protected route/helper/observer with synthetic providers.
The wrapper test also uses actual AuthProvider bootstrap; the service test uses
the real API401 interceptor and proves no refresh POST or replay. Local348
backend and114 frontend tests pass with no skips. Dedicated actual replica CI
and independent review remain pending. No live account, grant or provider access
was observed, no deployment happened.

First checkpoint372 had a workflow selection defect:319 tests passed, but its
asserted418 count failed because81 existing owner unit and18 new Mongo cases
were absent from the command. The successor adds those two files; no weakened
assertions or skipped Mongo acceptance. Dedicated CI must prove418 total and70
actual Mongo cases, plus114 frontend tests and a governed CI build.
