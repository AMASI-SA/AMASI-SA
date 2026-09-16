# Snapchat 30-day sync release checkpoint

## Scope

Fix the Snapchat Ads 30-day synchronization path so a refreshed access token is reused across subsequent request headers and 7-day windows. This prevents repeated refresh attempts from leaving part of the selected range at zero.

## Reviewed implementation

- Source PR: #1048
- Production source merge: `6190081644c65e584d1319ee88de42acf1da1443`
- Runtime file: `backend/integrations_control_center/snapchat_native_data_common.py`
- Regression test: `backend/tests/test_snapchat_native_data_sync_v2.py`

## Verification

- Python compile checks passed.
- Targeted refreshed-token reuse regression passed.
- Ads Auto Sync, Snapchat Settings Management, Security Gate, Release Readiness, and CodeQL passed for the reviewed source.
- No live Snapchat synchronization was triggered while preparing the release.

## Release boundary

This checkpoint contains no credentials, customer data, ad spend, or environment values. The governed v5 release intent is generated separately by GitHub Actions and committed as the intent-only handoff.