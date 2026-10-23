# Assembly board recovery — release prepared; production unchanged

Task branch: codex/assembly-board-recovery-20261022. Draft PR: https://github.com/AMASI-SA/AMASI-SA/pull/1460
This status is recorded on the documentation-only branch codex/assembly-board-release-status-20261023 to preserve the frozen deployment chain. Do not deploy this documentation branch.

Frozen v5 chain:
- Base J: 571e1704fc823c681c61b2f31de1db4f5ea5f55a
- Source A: 517868b0e1ea3c50466f59b992b12fe29c7e9a43
- Intent-only B: d2c8d78219b5d0d698f2fd872ac8c2355a7b8257
- Intended release: rg5-6347bb1f417fabf1eed44007799d57cffc294562d91b6b899f6116e0a34800f7
- A...B is one commit and only release/release-intent-v5.json. Do not squash/rebase.

Behavior: old native assembly boards now have a candidate server optimization using existing tenant-scoped get_orders instead of sequential per-order reads. Search narrows workflows before downstream piece/detail reads. Existing 5000-workflow window, substring matching, eligibility, ordering and paging remain. No order/status/financial writes.
Changed source: backend/preparation_piece_operations.py and backend/tests/test_review_local_downstream.py.

Release audit remediation: Black 26.10.1, certifi 2026.10.19, Pillow 13.0.0 in byte-identical backend/requirements.txt and scripts/release_backend_requirements.lock; Vite 8.2.4 and follow-redirects 1.16.1 in frontend declarations/lock. Compatible PostCSS 8.5.26 and Rolldown 1.2.6 retained. Governed runtime fixture derives Vite version. Security Gate runs existing image/PDF regressions with its audited dependency graph. No security exception or weakened threshold.

Verification completed:
- All 17 applicable workflows SUCCESS on A and B; TikTok candidate skipped by design.
- B readiness run 39998969599: backend preflight, frontend build and clean-clone Host Node 20 adapter rehearsal (job 120570685411) SUCCESS. Reviewed identity, package boundary, isolated runtime and health verified. Manual redeploy job skipped; this is not publication.
- Exact-source real-Mongo board/search tests passed; shipping boundary and auth/passkey workflows passed.
- Security Gate strict pip-audit: no known vulnerabilities. Frontend production audit: zero blocking findings/no exceptions. 287 frontend suites and 1949 tests passed.
- Canonical image/PDF: 76 passed, one Mongo-dependent case skipped; Google image: 5 passed.
- Local board/shipping: 103 passed, 46 Mongo-dependent skips, 22 subtests. Local governed runtime tests: 28 passed. Local two clean frontend builds matched the CI-reviewed artifact hashes.
- Local full-adapter rehearsal was refused due missing historical Git parent b4e5d7a39951f82dff3010f33ade210714bd70c8 in the reconstructed clone; no guard was bypassed. The clean-clone CI rehearsal is the completed full-adapter evidence.
- Independent read-only candidate review completed; no concrete introduced regression found. Some older CI-only Pillow pins remain; do not count those jobs as Pillow 13 coverage.

Observed production (read-only): live health source bc85eddf3d5c92b370c2f59d17ac79946763863e, release rg5-331c2f714358404dd155a58527d8a5334821b302ee292459c2c5ca11a9eec50d. Production Git branch remains J above. No deployment or release lease started by this task.
Blockers: Emergent deployment access; actual backend interpreter must be proven Python >=3.11 for Pillow 13. CI 3.11/local 3.12 are not production proof. Earlier declined login must not be retried silently.
Next safe action: restore authorized deployment access, verify actual runtime, recheck production base/active lease, then follow AGENTS.md v5 prepare/prepublish/publish and three consecutive production verifications/close. Retain prior verified deployment for rollback. Do not deploy this status branch.

Android 55 (1.0.40) is now installable:
https://expo.dev/accounts/amasi-gulf/projects/amasi/builds/9191f7b2-fe8b-4485-bfc1-09eae1f6d80a
Expo visibly reports Succeeded / APK / Install on 2026-10-23.
Compiled mobile source: 08ceebcee1d24939b005e731c58db8cc8413af9f; final documentation/proof checkpoint: b55ed514fd92130673d4d72dfd9a05c5f40fc6ff.
Mobile CI 39995945207 and shipping CI 39995946067 passed.
Native spinner settlement and explicit search are fixed in this APK. Physical phone acceptance still pending. The Mezan WebView stays experimental; shared login/session persistence remain unresolved.
Mobile PR: https://github.com/AMASI-SA/amasi-mobile/pull/294

Issue #1006 rejects further comments at its limit; PR 1460 is the fallback continuation ledger and links this status checkpoint.
Production server changed: no. Android internal APK availability: yes.
