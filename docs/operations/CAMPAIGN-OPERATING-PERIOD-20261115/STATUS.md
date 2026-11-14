# Campaign operating period correction — source verified

Task: CAMPAIGN-OPERATING-PERIOD-20261115

Repository: AMASI-SA/AMASI-SA; PR #1932; source branch codex/campaign-operating-period-20261115.
Source A: af36682ec95e578764242001dccd16ef9e61c07b; source tree: 218fa9d21d863783405dc227793ad7f9bbd1b7a5; production baseline: 324dab9fd01fab17a28a5f7de4bb2bd298d7aea9.

All 24 exact-source workflows passed on attempt 1. Campaign source coverage executed 272 backend tests and 88 frontend tests (10 suites). Dashboard cadence executed 269 backend tests plus 5 subtests and 251 frontend tests (20 suites); overlap is not summed. Root and independent source reviews passed.

The operating allocation uses the visible executive period and normalized cohort, including cache identity and pending/stale response protection. Standalone order pages observe the current Riyadh day on their existing data refresh. No data/provider synchronization, ad writes or scheduling changes were made.

Root reviewed all 2,690 source records and canonical manifests of the fresh source intent. Independent intent review is pending. Intent SHA256: e971d2ce2f801e531f7541bb31cbbf963635584369c0c1208092a389d8ac1bb2; runtime ID: rg5-3c726702879630f436b0b615a4e2d1dd85ff5889c45b43b1be477f84cb3043fe. No intent-only B, merge, lease or publication exists yet. Production remains at 324dab9fd01fab17a28a5f7de4bb2bd298d7aea9.

The read-only verifier initially used a public subset where the contract hashes the full frontend artifact, and omitted one helper argument. Those verifier assumptions were corrected; source and intent bytes were unchanged. Actual package bytes and JSON routes require B host artifacts.

Next: independent intent approval; freeze only release/release-intent-v5.json as B, then all B CI and both host artifacts. Never deploy this evidence branch.

Issue #1006 has reached its comment limit; this STATUS, checkpoint and PR #1932 form the continuation record. Preserve the 120 unrelated original workspace untracked files, original terminals and unsaved editor buffer; original read-only guard status was inactive. Disabled operator templates must be freshly pinned and independently reviewed before execution.
