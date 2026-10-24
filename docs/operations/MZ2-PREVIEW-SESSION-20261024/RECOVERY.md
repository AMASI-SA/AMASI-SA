# Preview exact-host recovery — 2026-10-24

Private runtime: /app/.worktrees/mz2-uat-1128-runtime-20260921/preview_password_runtime.py

Applied replacements (exactly two occurrences):
`salla-analytics.cluster-12.preview.emergentcf.cloud` → `salla-analytics.cluster-9.preview.emergentcf.cloud`.

Added `request_host: hostname` to the existing once-per-process sanitized rejection diagnostic. Never logs cookies, tokens, body, query or client address.

Matched adapter.sha256 to 551b4a0549bca9186e92c63041e01910df7388ffd3d50c3e7c9b67f239b70f6d. The launcher's integrity check remains enforced.

Before-change backup: preview_password_runtime.before-host-diagnostic-20261024162853.py and adapter.before-host-diagnostic.sha256 in the same private directory. Recovery record: host-recovery-20261024.json.

Eight actual ASGI tests passed: public Host, current cluster Host, current cluster Origin and public Origin accepted; production Host, old cluster Host, production Origin and null Origin rejected. Restarted Supervisor backend (Preview only). Public health returned 200; browser existing session loaded the demo dashboard. No new password, session forgery, MFA policy change, data writes, production deployment, or writer activation.

Future gateway moves require observing the exact project Host again; do not wildcard the Emergent suffix or disable origin/CSRF checks. Review Preview version and worker isolation before training; login recovery does not prove current Production accounting features exist in the old isolated snapshot.
