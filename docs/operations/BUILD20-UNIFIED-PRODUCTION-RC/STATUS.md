# Build20 Unified Production RC — source checkpoint

Branch: integration/build20-unified-production-rc-20260927
Draft PR: https://github.com/AMASI-SA/AMASI-SA/pull/1163
Production Git base: 096edc75f398d6d48b5cc8e4189354af10342cbc (Qoyod PR #1158).
Accepted Build20 source: 76da9972fedc419e1058af2dd454cf804399d739.
Integration cherry-pick: 30f97b8c.

The Qoyod manual send and queue_select files have the exact blobs from the new base. All 19 accepted Build20 paths have the exact blobs from the old Build20 RC. The base is a valid v5 Qoyod intent commit; its governed source is ff4e7011e6ff411974927eef839fa58b53f8e3e4.

The new Qoyod CI gate tests manual send and backlog in independent pytest processes. On the unchanged base, a combined process gives one test-order failure (66 pass, 1 fail), while separate processes pass 44 and 23 tests. The unified branch passes those same separate local suites. No Qoyod product or test file was changed to address this harness interaction.

Full exact-head CI on this source checkpoint must pass all required workflows, including Qoyod payment freshness, supplier contracts, security, CodeQL, and release readiness. The inherited release/release-intent-v5.json belongs to the prior Qoyod base; a new intent will be frozen only after the unified source passes the full gate, followed by an intent-only commit.

No Release Guard prepare, Production publish, Re-publish, or Android build has occurred in this task.
