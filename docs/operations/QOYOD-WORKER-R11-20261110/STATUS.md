# Qoyod worker R11 — diagnostic WIP

Repository: AMASI-SA/AMASI-SA. Branch: codex/qoyod-worker-failure-diagnostics-r11-20261110.
Base: ec79949e36e7f636ff4ba8246e4413e7d54bcd6a (deployed R10 #1864).

User requests guarded automatic invoice delivery approximately every two hours,
with header counts refreshed only on explicit click. Prior merge/deployment and
eligible invoice sending authorization remains applicable. This patch does not
change either cadence or financial write/authorization/duplicate checks.

R10 is published and officially verified:true; owned release lease closed.
Owner recovery bound successfully. Full provider scan failed twice without a
usable diagnostic. No invoices sent by this task. Local unmatched count is not
proof of provider absence.

Read-only production observation on November 10 at 17:53 UTC: the cycle started
November 9 00:59 UTC remains running/sending, 129 batches, 184 attempts, 773
eligible, 529 processed/review, zero sent. Consecutive auto-run audit documents
repeated the same five candidates and remained in_progress. This does not yet
prove whether interruption is a worker exit, lease loss or provider delay.

Implemented diagnostic slice:
- Sanitized provider exception class/status, no raw response/message.
- Scan failure stage retained without replacing the last successful report.
- Existing worker status records completed job/monitor name, outcome and error
  class before shutdown. Best-effort write has a two-second bound; original
  restart/cancellation semantics remain intact. No polling or new collection.

Verification: focused 20 tests passed, then expanded suite 33 passed / 10 skipped
(real isolated Mongo unavailable), exit 0. Tests cover actual async scan failure,
preserved successful report, secret-message exclusion, worker/monitor failures,
and diagnostic-storage failure. The regression initially failed because the
worker helper and scan diagnostics were absent. git diff --check passed.

Production changed by R11: no. No R11 merge, release intent, lease or deployment.
Root automatic-send interruption remains unresolved; diagnostics are not a fix
claim. Next: remote checkpoint and Draft PR, exact-head CI; complete safe
diagnostics review and governed v5 release if needed to obtain actual failure;
then reproduce/fix that cause and verify cycle progress/provider receipt.
Issue #1006 is comment-limited; this document and PR body retain continuity.
