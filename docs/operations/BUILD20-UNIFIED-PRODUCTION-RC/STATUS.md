# Build20 Unified Production RC — checkpoint
Task: Build20 backend + Qoyod PR #1158 in one Production candidate.
Branch: integration/build20-unified-production-rc-20260927
Base: 096edc75f398d6d48b5cc8e4189354af10342cbc (PR #1158).
Accepted Build20 source: 76da9972fedc419e1058af2dd454cf804399d739 (19 paths).
Initial integration commit: 30f97b8c (cherry-pick onto the base).
Qoyod manual send and queue_select match the new base byte-for-byte.
Build20 files match the accepted RC; only Qoyod files/tests and prior intent differ from the old RC.
Remote checkpoint: branch pushed; full CI and final source freeze pending.
The inherited release/release-intent-v5.json is stale for this combined candidate and must not be used to publish.
Next: Draft PR, full combined CI, final source commit, then a new intent-only commit.
Production changed by this task: no. No Release Guard prepare or Re-publish.
Qoyod exact-head backlog/manual CI workflow added; outcome pending on Draft PR #1163.
