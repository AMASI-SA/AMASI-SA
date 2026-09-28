# R4 native integration recovery checkpoint — NOT a release

Continues materialized R3 on parent `e0ce8e46e63541866ac250b25b0ce2a290a64256`.
This exact 15-file patch is an intermediate source transfer for independently
running native tests and storing source blob identities. The source files must
then be materialized into ordinary backend paths and committed before calling
this raw branch an implemented/tested R4 source. Do not reapply it after that.

Implemented in the transfer: native supplier/inventory acceptance tests; actual
piece/file transaction selector fix; native standalone driver handover/pickup/
delivery integration; COD-versus-receivable and snapshotted driver fee posting;
immutable native source read-back; fresh persisted driver/accountant permission
checks; noncash receipt approval with mandatory bank movement; receipt rejection
and resubmission with immutable original evidence and no premature bank cash.

Local incremental observed tests: supplier3, inventory4, transaction-selector5;
driver lifecycle4, noncash3, approval rollback1, resubmission/permission2. These
ran on a dedicated loopback replica set with synthetic records. Final combined
candidate and frozen ordinary-order regression remain to run in this workflow.
One new test initially used the wrong receipt-response field (`token` instead of
native `receipt_reference`); corrected without changing the native wire response.
Some local combined runs hit the execution tool timeout; no timeout is a pass.

Still required: native settlements and reassignment compatibility, complete
preparation/assembly/actual label/PDF flow, persistent Salla-balance sidecar,
report consumers and client compatibility/UAT/rollback. No claim of completion.
No server.py registration, runtime enablement, permission widening, merchant DB,
Salla/provider/financial live writes, Merge, Preview/Production deployment or
release guard/lease/intent changes. Draft PR1171 and independent UI1167 preserved.

Archive: 19124 bytes, five ordered .xzpart files.
SHA256 ca9799c7a1c771923bf49f32cb2e3c41129dc06f24ea414e09a0c0dbe970117c
JSON SHA256 62ac1371383e7ff6982182a75a485b4b828d0975ef523e64e0b6187033c65f28
Materializer checks all predecessor and result source hashes, approved paths and
branch. Export runs only after successful tests and creates blob objects only;
it has no ref/merge/deployment action. Actual source identity is independently
verified against Git's blob hash before native paths are materialized.
