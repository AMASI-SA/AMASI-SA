# MEZAN-LIVE-SOURCES-20261118

Scope: add a first-party visit-source card immediately below active GA4 users in AdvancedDashboard. Production changed: no. No /app writes, release lease, snippet publication, ad writes, or order-attribution changes.

Branch: codex/mezan-live-visit-sources-20261118
Base: 68035e55 (origin/hotfix/prod-snap-meta-final at initial fetch).

Implemented:
- Authenticated tenant-scoped GET /api/first-party-attribution/v1/live-sources.
- Production events only, server received_at rolling 30-minute window; compound index; max 20,000 events and 2.5-second Mongo limit. Exceeding the limit returns unavailable, never partial counts.
- Unique store/visitor/session tuples, classified from first observed URL/referrer inside window; no use of the snippet's 90-day acquisition source as current traffic evidence.
- Snapchat, Meta, TikTok, Google paid/organic/unspecified, direct/no visible referrer, other referrals/campaigns, unknown. UTM/click signals are browser-reported evidence, not independently verified ad conversions.
- 30-second per-owner process cache; per-owner single flight, up to 16 in-flight owners; max 128 cache entries. No background scan or platform calls.
- Card reads on mount/visible-tab return and every 60 seconds while visible; abort on unmount; no overlapping reads; empty/error/stale distinctions and reading timestamp.

Verification:
- PYTHONPATH=backend python -m pytest backend/tests/test_first_party_live_sources.py -q: 16 passed using existing mezan-profit-py environment.
- CI=true react-scripts test --watch=false --runInBand --runTestsByPath src/components/MezanLiveSourcesCard.test.jsx src/pages/AdvancedDashboard.mobile.test.jsx src/pages/AdvancedDashboard.cardRefresh.test.jsx: 43 passed. Existing frontend node_modules reused read-only via symlink.
- Existing backend/tests/test_first_party_attribution_v1.py: 7 passed, 2 failed both on pristine base and working branch. Those tests use August fixed event times outside current 30-day matching window. No changes to matching behavior.
- Local `vite build --outDir /workspace/scratch/47769d1a75d9/mezan-ui-check`: PASS (exit 0, 8.38s); existing large-chunk/dynamic-import warnings. This is not a governed deployment artifact.

Limits / next steps:
- No production or real Mongo explain/live-event verification yet; post-deploy check must verify index usage, latency and a tagged session counted once while navigating.
- First entry can predate the 30-minute read window; internal-referrer-only evidence then stays unknown. No claim of full journey attribution.
- Direct means no visible referrer, not proven direct navigation. Unknown aliases remain other campaigns. Browser blockers/consent may limit collection.
- Pilot excluded; older events missing production store_scope are deliberately excluded. No collection schema or production snippet update required.
- Requests are frontend-visible-tab scoped, not viewport/intersection or collapsed-card scoped.
- Reply to Salla about order matching remains independent; this card does not measure orders/profit or prove checkout coverage.
- Review draft and CI; deploy only through repository v5 protocol after authorization. No release intent is frozen in this feature branch.
- GitHub issue #1006 body read. Fetching its full comments failed (transport closed); new task has no prior task checkpoint known. Record this branch's exact commit in the ledger after push.

Owner authorized deployment. Refreshed source onto production 8761f7b2f80faec29638e1438b8fedefcec64a28 preserving Aramex and Snapchat releases. Next: governed A/B build, intent-only B, CI, merge and guarded deploy. PR #1990 is continuation ledger (issue #1006 reached comment limit). Production changed by this task: no.
