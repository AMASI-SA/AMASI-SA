# Campaign operating period — merged, not published

Task CAMPAIGN-OPERATING-PERIOD-20261115; PR #1932.

Source A: af36682ec95e578764242001dccd16ef9e61c07b
Intent B: c8d70f26ab02bc9467c844fee477ef040688edb2
Deployment merge M: 7ef45017e5221635ce3c80c3788f73d9c50b1e08
Deployment tree: e55cdf2c2b15cfe08efe3e0305d043e87f916d8b
Parents: 324dab9fd01fab17a28a5f7de4bb2bd298d7aea9, c8d70f26ab02bc9467c844fee477ef040688edb2.

Source A's24workflows passed. B has28run receipts:27successes and1expected TikTok skip. Stock and Balance initially failed one unchanged CPU-ratio benchmark each; one exact-B retry each passed without source/test/threshold changes. Both initial failures and retries remain recorded. Both actual host artifact sets passed root and independent acceptance:293identical generated files, full289frontend-content records including headers, exact identity/proof/metadata/package boundaries, and2actualJSONroute checks.

Normalmerge preserves reviewed A/B ancestry and exactBtree. Push Readiness/Security/CodeQL are pending. Pinned original-build operator is saved with exactM/artifact pins, but independent final-byte review and push gates are required before execution. Original lastread-onlystate21:53:20Z:inactive,M6,trackedclean,120untracked. Original has not been changed; no lease or publication. Live UI baseline75ee080/100th remains old.

Next: finish3exactMpushgates and operatorreview, refreshinactiveoriginalstatus, execute the pinned originalbuild once. Only after successful preserved originalbuild may a freshly pinned guardrunner prepare/prepublish, one authorized free UI publication, and official verify3/inactive occur. Preserve all unrelated files/terminals and unsavedbuffer. Never deploy this evidencebranch.

Issue1006has reached its commentlimit; this STATUS/checkpoint/PR1932are continuationrecord.

## Ready for original governed build

All three exact-M7 push gates passed on attempt 1. The final original-build operator has passed independent byte, AST, ancestry and artifact-pin review. Its SHA256 is 88c17d513c5549b797f06840465572ad285ce31a3b1d4ec25f31d633895de4b2 (15,520 bytes). No original build, lease or publication has started. Next: a fresh inactive status and expected M6/clean/preservation checks, then execute this pinned operator once against M7. The operator performs its own locked checks and records actual exits.

## Original build started

At 22:07:26 UTC the fresh preflight passed: inactive guard, expected M6, clean tracked tree, and 120 unrelated untracked paths. The reviewed original-build operator was fetched by immutable H commit and verified against its exact bytes/hash before its one invocation.

The original build is running in Terminal 4. Evidence directory: `/tmp/mezan-operating-period-original-iljwu1fd`. Do not assume HEAD remains M6 and do not rerun the invocation. Inspect `result.json`, `failed.json` and actual audit command receipts first. No lease or publication has started.

A gate operator is now pinned to that exact evidence directory, M7 identity and observed UI baseline 75ee080. Independent final-byte review is pending. Its code is saved for continuity, not authorization to execute before original success.
