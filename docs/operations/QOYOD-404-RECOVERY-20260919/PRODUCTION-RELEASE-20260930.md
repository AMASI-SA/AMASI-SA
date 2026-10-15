# Qoyod historical recovery production release — 2026-09-30

- The Qoyod historical-order recovery source was merged in PR #1194.
- This source commit introduces no runtime or application-code change.
- Its sole purpose is to stage a fresh Release Guard v5 source identity above Production after the prior intent branch metadata was rejected.
- New-order Qoyod delivery behavior remains unchanged.
- The following commit must change only `release/release-intent-v5.json` and bind this source commit to `hotfix/prod-snap-meta-final`.
