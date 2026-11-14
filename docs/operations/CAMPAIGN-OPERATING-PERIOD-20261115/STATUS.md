# Campaign operating period correction — ready to publish

Task: CAMPAIGN-OPERATING-PERIOD-20261115. PR #1932 is merged.

## Immutable release

- Source A: af36682ec95e578764242001dccd16ef9e61c07b
- Production base: 324dab9fd01fab17a28a5f7de4bb2bd298d7aea9
- Intent B: c8d70f26ab02bc9467c844fee477ef040688edb2
- Deployment M7: 7ef45017e5221635ce3c80c3788f73d9c50b1e08
- Deployment tree: e55cdf2c2b15cfe08efe3e0305d043e87f916d8b
- Runtime release: rg5-3c726702879630f436b0b615a4e2d1dd85ff5889c45b43b1be477f84cb3043fe
- Intent SHA256: e971d2ce2f801e531f7541bb31cbbf963635584369c0c1208092a389d8ac1bb2
- Runtime identity SHA256: 0662d49ed6abfc16074d1033667e19df182f7189698c285689cb431ea1a05ed3

M7 has parents [base, B], the exact B tree, and an intent-only content difference after A. Never deploy this evidence branch.

## Verification complete

All 24 source workflows passed. All 28 B run receipts are complete: 27 successes and one expected TikTok skip. Two unchanged CPU timing assertions failed their first attempts and passed one controlled retry each; source and thresholds were unchanged. All three exact M7 push gates passed on their first attempts. Both actual host artifact sets passed root and independent content, metadata, identity, proof, package and JSON route acceptance.

The original governed build finished at 2026-11-14T22:08:34.851568Z. Adapter exit 0 in 64.0004 seconds; package verification exit 0 in 2.1645 seconds. Package-boundary bytes match the B host artifacts. Its result SHA256 is 135848f377199ee9da945745e71de161c7d83c1e7be9e0ca9a07384595507b0f; no failed.json exists. Original HEAD is M7; the guard remained inactive. All 120 unrelated untracked paths were preserved (119 exact records and the designated append-only bookkeeping file; 50,912 bytes before and after). No user data/provider/ad writes occurred.

Original evidence directory: /tmp/mezan-operating-period-original-iljwu1fd. Original build has completed; do not run it again.

## Next controlled phases

The final gate runner has passed independent byte and AST review. Path: operators/campaign-operating-release-gate-1932.py. Bytes: 29,142. SHA256: ffe7b11e43ad618d1b995d9afb2c33311cbb0ec35642795ad673373f996320fb. It is pinned to the successful original directory and observed UI baseline 75ee080.

No prepare, prepublish, verify or publication has run for this release. Next: fetch this exact evidence commit, verify gate-runner bytes, prepare once, inspect the actual receipt, and prepublish immediately before one authorized free UI publication. Require a new explicit successful deployment event and published version, then official verify with three consecutive probes and inactive post-status. Never repeat an official phase after a wrapper failure; inspect persisted outcomes first.

Live Production remains the previous published version until the UI publication succeeds. Preserve all existing terminals and the unsaved editor buffer. Issue #1006 is full; this STATUS, RELEASE_CHECKPOINT and PR #1932 are the continuation record.

## Lease prepared

The pinned prepare phase completed successfully at 22:10:58.125567 UTC with its actual receipt and preserved files. The matching v5 lease is active for actor `codex-campaign-operating-period-1932`, source A and deployment M7. Receipt SHA256: f9301058bfacfd505e1398d9e55e38aaa4fe9b7fa5828990d6a1a5efc56b0402. No prepublish or UI publication has run yet. Next: pinned prepublish once, then one authorized free UI publication. Do not prepare again or clear this matching lease.
