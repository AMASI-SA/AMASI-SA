# Campaign hover: exact source CI artifact verification

Source: `2cf4d70d7e0c23c11048cad5c610e8c0c60d7eeb`
Tree: `b18db6ce619970c2a5c11c86e332fd21199b6dd3`
PR: 1920
Observed: 2026-11-14T14:56:10.542Z

## Final official results

| Gate | Run | Job | Result |
|---|---|---|---|
| Operational Balance Backend | 43606645134 | 132482682700 | 4,672 passed; zero XML failures, errors, skips |
| Operational shipping calculator contracts | 43606645134 | 132482682700 | 16 passed and 18 subtests passed |
| Operational Stock Eligibility | 43606645215 | 132482686183 | 4,481 passed and 7 subtests passed; zero XML failures, errors, skips |
| Operational frontend | 43606645134 | 132482682747 | 733 tests / 51 suites passed; zero failed/pending/todo |
| Operational browser acceptance | 43606645134 | 132482682692 | 9/9 scenarios PASS; zero errors |
| Canonical worker | 43606645134 | 132482682499 | 4 passed; zero XML failures, errors, skips |

Both run APIs report completed/success at the exact source above.

## Evidence and verification

Downloaded ZIP SHA256 values match the GitHub artifact digest:
- Operational backend artifact 14118601765: `20706d4c9451b20e99fd2e31db511793c8814da739539be75cf742ce6d3d09aa`.
- Eligibility backend artifact 14119190815: `11d0d35ec2a399b2d88c7f6500e1f86bcd9bdbbaec6bd233857c16ab3e6b9e01`.
- Frontend artifact 14118309494, worker artifact 14118630593 and browser artifact 14118284522 also passed their digest checks; full values are in run-metadata.json.

Operational backend, worker and browser source files match the exact source and tree. Eligibility job logs record the same source and tree. The official logs show ephemeral loopback-only Mongo 8.0.12 replica fixtures (plus standalone Mongo for eligibility). The required no-skip checks passed; independent XML parsing also found no skipped, failed or errored testcase.

The operational and eligibility XMLs respectively contain 187 and 182 distinct reported suite/class groups. These are regression gates, not a claim that every individual test directly uses Mongo.

## Coverage boundary

Neither workflow collects `test_campaign_hover_profit.py` or `test_campaign_hover_operating.py`: both XMLs contain zero campaign-hover cases. Those committed functional tests run separately in Campaign hover source coverage and retain their mongomock fixtures. No gate was waived, retried or changed during this read-only verification.

The superseded source `2c26dea8` evidence is retained in a separate sibling directory and is not counted as gate evidence for this source.
