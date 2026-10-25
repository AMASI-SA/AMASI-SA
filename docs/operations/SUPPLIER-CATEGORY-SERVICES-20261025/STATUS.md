# Supplier categories and optional services

## Verified source checkpoint

Production has not changed. Branch `codex/supplier-category-services-20261025` starts at `04db2224d68d6e5ed5629391355c4b45ab8b6d15`.

Web and mobile supplier APIs now share explicit, tenant-owned category/service validation. New and edited suppliers require one or more active categories. Services are optional and must belong to at least one selected category. No automatic assignment of every service in a category. Category-only suppliers may receive products in their selected canonical product cost categories. Existing service-only suppliers retain their previous dispatch eligibility until an operator explicitly classifies them; no live migration is performed.

The web editor displays category selection before optional filtered services. Fixed its undefined `financialData` reference, which crashed Add Supplier. Financial persistence, payment gates and Ready behavior are unchanged.

Fresh verification: supplier category routes, supplier management, dispatch and financial display boundary: 71 tests PASS; web editor and service client: 12 tests PASS; diff whitespace check PASS. Tests use isolated synthetic data, no live supplier/order/financial writes. Native supplier work is separately preserved; the user subsequently delegated APK work to the other conversation.

## Authorized integration / remaining work

User authorized combining this work with invoice-numbering PR1508 and a governed server release, explicitly without enabling financial writers. Latest fetched Production is `f01beac1e` (PR1512), which must be preserved. PR1508 source `7a59211d34cf03f701b00c279abdceb77202706e` has a failing Frontend build: candidate A/B ancestry validation, not an application compile failure. Its side-branch merge history includes intent revisions, so transplant only the two invoice source commits onto the latest Production-based integration branch; do not weaken Release Guard or reuse its stale intent.

Next: checkpoint remotely, integrate fresh Production and invoice source commits, run combined supplier/numbering tests, create reviewed source A and intent-only B, pass CI. Check shared `/app` lease and publishing state before remote modification or release rehearsal. Publish only with official prepare/prepublish, visible Deployment Succeeded and three-probe verify. Preserve Preview-specific opening-balance work and all unrelated untracked files. APK build and signing are outside this conversation's latest scope. No release lease has been created by this checkpoint.
