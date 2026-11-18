# MEZAN-LIVE-SOURCES-20261118 — deployed and verified

Final checkpoint: 2026-11-18 (Asia/Riyadh). PR #1994 merged and published.
PR: https://github.com/AMASI-SA/AMASI-SA/pull/1994

## Release identity
- Source A: a206a3ede48ab84690bd3d1b055f8e3babbfec0e
- Intent-only B: 4b32064f55fbf3aad85d6953209ae4b256ba6ee6
- Merge/deployment: d3632a33843e076dd7824933528dcc7d897bc32a
- Previous production: 8761f7b2f80faec29638e1438b8fedefcec64a28
- Release: rg5-9a4b94923b15b4c3d25e35c7af4efe8dea5e1de595286e8bec8fd317b70761d8
- Emergent published version: b314650
- Publish clicked once at 22:29 Riyadh; UI showed Deployment finished at 22:40 Riyadh.

## Verification
- 16 backend and 43 frontend feature tests passed.
- All 24 applicable CI workflows passed.
- Governed double build and clean-clone rehearsal passed; CI clean clone Node 20.20.2 and 24.19.0 passed.
- /app fast-forward, adapter build, prepare and prepublish passed.
- verify --url https://mezansalla.com returned exit 0, verified=true, checks=3.
- All three production rounds matched frozen source/deployment/release and frontend artifacts.
- Health: verified_identity_available=true, critical_file_hashes_match=true, frontend_build_verified=true.
- Final production_release_guard.py status: active=false.
- Frontend artifact tree: c7b4cdb4f6717c54b22508e593869b97b5a50106d82468b1013c9fb8444d49b3.
- Live card visible below active users and automatic refresh observed. Example at 22:42:38 Riyadh: 1104 sessions, Snap 411, Meta 82, TikTok 18, organic Google 6, direct/no referrer 549, unknown 38.

## Semantics and limits
Authenticated tenant/store-scoped production events, rolling 30 minutes by received_at, distinct store/visitor/session tuples. Source from earliest observed URL/referrer in window, not 90-day acquisition source. Classification percentage is not accuracy. Direct means no visible referrer. Sessions are not GA4 users or attributed orders. No order/profit attribution or snippet change.

30-second bounded owner cache/single flight, 20k-event and 2.5-second Mongo limits; 60-second visible-tab polling with cancellation. No ad-platform calls or background worker.

Still separate follow-up: real Mongo explain/index latency verification and controlled tagged-session navigation/dedup end-to-end test. Unit coverage and live data do not prove full journey accuracy or checkout coverage.

Issue #1006 is full; PR #1994 comments are continuation ledger. This docs-only handoff is kept on a separate branch to preserve frozen A/B and the deployed production commit.
