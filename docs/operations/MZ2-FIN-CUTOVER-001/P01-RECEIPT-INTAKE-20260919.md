# P01 receipt intake — Preview acceptance checkpoint

P01 remains IN_PROGRESS; P02 remains locked. No Production publication or merge.

## Candidate and scope
- Base: e82918d130f42b228346b2cbd3d70ff4daf21bf3 in draft PR #1091.
- Stacked draft PR #1097, branch codex/p01-settlement-intake-20260919.
- Verified application tree: 2b0264cf06b6b935343742d723abcf7132e06828.
- Both Preview runtime identities match this tree. The host-bound recovery launcher,
  independent session key and loopback replica-set configuration remain outside
  application code. External network/automatic workers remain disabled.
- Previously uncertain synthetic draft: read-only database examination established
  reviewed/unposted with no matching journal. It remains unchanged by instruction.
- All pre-existing ledger rows and the three protected settlements were preserved.
  Private references, balances, snapshots and authentication fixtures are outside Git.

## Implemented
Shared non-ledger bank-receipt intake from daily movements; verified provider-bank
selection; labelled bank-reference extraction; separate statement reference; either
arrival order; explicit one-to-one receipt/statement linking; no automatic choice
among candidates; missing evidence or amount differences block progression.
Receipt creation and statement upload do not post a journal. Entry, review and
posting are independently permissioned. The API/service is reusable by a future
mobile client; no mobile UI is included.

The active first-installed lifecycle post handler previously lacked the transaction
wrapper applied to its compatibility handler. It now commits journal, draft state,
receipt consumption and audit in the same existing owner transaction. Added actual
Mongo coverage injects a failure after journal creation and verifies full rollback,
successful retry and no repeated bank effect.

Browser acceptance found stale saved blockers after receipt linking; recomputation
now persists with the link. The settlement register now displays receipt evidence
and bank/statement references separately.

## Fresh evidence
- Actual Preview browser: receipt-first and statement-first scenarios completed
  through manual-tax sale recognition, matching, review and one balanced posting.
- Actual Preview browser: multiple candidate receipts leave selection blank;
  mismatching candidate displays the difference; repeated receipt submission keeps
  the same identifier; both posted results survive page reload and service restart.
- Actual Preview HTTP with dedicated password-authenticated test roles: entry/view
  cannot review/post, viewer/poster cannot intake, other-owner read is denied,
  repeated post is rejected with no financial writes.
- Actual Preview HTTP: concurrent identical intake requests and retry after a fresh
  login return one receipt; conflicting retry rejects; amount-difference submission
  rejects without a financial write.
- Actual Preview original-file view matches the uploaded workbook. HTTP download
  byte count and SHA256 match the original; another owner receives 404.
  Browser download event/save completion remains unverified (tool timeout).
- Dedicated real Mongo receipt suite: 10 pass; independent synthetic databases.
- Targeted P01 backend regression suite: 49 pass.
- Four frontend suites: 16 pass; full Vite Preview build passes.
These are targeted tests, not a claim of complete repository pytest coverage.
Injected-failure/core recovery tests are distinguished from live browser operations.

## Remaining gate
Keep P01 open. Complete browser-saved-file proof and any remaining written P01
browser/recovery acceptance. Review the stacked candidate and integration risks.
Production-branch merge, guarded publication and post-publication verification are
separate release requirements and are not authorized by this Preview task.
Synthetic acceptance does not attest to original bank/provider statements.
