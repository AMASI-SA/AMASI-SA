# Verification follow-up

At source 5069dec55cc5f1aa46d307c8cb5a7a9b75ac9c88:

- Full Frontend: 242 suites / 1390 tests PASS; ordinary Vite compile PASS.
- Connected browser: lost-upload-response/idempotent retry and view-only denial PASS; exact note label lookup failed after typing `ab`. Separate label/control association fixes the observed accessibility problem; regression now reacquires the label after typing and preserves short-note rejection. Focused reviewer suite: 9 PASS.
- Browser cleanup: fixture server exited, its UUID database removed, C3/financial/control hashes unchanged. No Production requests.
- Backend: no tests started. Windows Mongo `serverStatus` returned malformed diagnostic BSON; verification now checks the live owned child and exact unique dbpath/loopback options without relaxing BSON decoding. The previous child/listener were identity-verified and stopped; files retained.
- Track F CI [36948876786](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36948876786): 721 PASS + 549 subtests; five optional-delivery setup errors because the real mobile principal imports the existing PDF module. Add the already-used pinned PyMuPDF dependency to this isolated workflow; retain all real bridge tests.
- Release Readiness [36948876785](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36948876785): FAIL CLOSED, stale source intent and invalid candidate transition. Guard unchanged; diagnosis/valid source-intent sequence still required.

External raw evidence: `C:/Users/amasi/mz2-late-evidence-proof-20261002/final-frontend-5069`, `final-browser-5069`, `final-backend-5069`. These failures are retained, not relabeled PASS. Exact-source reruns follow this checkpoint.

Production financial writes = 0. Merge/Deploy/Opening/Activation = NO; write-control unchanged; Release Readiness = NO.
