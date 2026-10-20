# Review eligibility guard

Owner contract: new review approval requires a fresh Salla waiting-review status. Approval remains local in Mezan; no Salla status writes. Every other/unknown status rejects with its name; unavailable reads reject without approval.

Base: 571e1704fc823c681c61b2f31de1db4f5ea5f55a (hotfix/prod-snap-meta-final). Dedicated branch codex/review-salla-pending-guard-20261021.

Added a bounded single GET immediately before the local completion service, identity checks, conflict message, and transaction-local rejection if a newer webhook advances status. Completed/readback retries retain original proof without new provider I/O. The provider has no atomic cross-system status lock; a remote change after GET and before local commit remains a residual race until observed by the local source fence.

Verification: 19 focused tests PASS; Python compile and git diff check PASS. Real Mongo HTTP tests added for stale-local/live-delivered, provider outage, and completed retry; full integration CI pending. Existing tests with obsolete no-provider-read expectations may require explicit transport-fixture updates, not production exemptions.

Production changed: NO. Not merged, no intent, no lease, no publish.

Rollback: no DB migration or destructive data changes. Revert only this task's runtime diff onto the then-current production source, preserve all review/event/order data, create a fresh v5 A/B release intent, rehearse, and publish via Release Guard. Do not reset shared production or reuse an old intent. Rollback not rehearsed yet.

Next: inspect PR full integration results; resolve regressions; verify UI displays server detail.message; create/rehearse forward and rollback release packages; inspect /app guard status then authorized v5 publish and three identity probes.

Issue1006 has reached GitHub's 2500-comment limit; this task document and Draft PR are continuation records.
