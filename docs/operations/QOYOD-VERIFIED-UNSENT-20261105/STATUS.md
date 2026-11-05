# QOYOD-VERIFIED-UNSENT-20261105

## Lightweight banner follow-up (2026-11-05)

User explicitly requested protecting fulfillment performance and authorized merge/publish.
Branch: fix/qoyod-lightweight-header-current-20261105. Supersedes PR #1766 and unmerged PR #1764;
includes its observational pagination compatibility fix without its frozen intent.
Base production: fb43ce8af094bff529ecd8499c2537bc9c70b74b.
PR #1765 courier/cost publication advanced the base during the auth wait.
Both source commits applied cleanly; old #1766 intent was not merged.
#1766 clean server adapter and all 11 applicable CI workflows passed.
Rebuild and freeze a new intent on this latest base; preserve #1765 intact.
The first publication is verified: three consecutive Release Guard probes passed
for source 4b8709f2448995302477d9125708b14ce757d7de, release
rg5-de4f90c9aa5b48797c7235ddc5cac1404f9ecd58b09bcbcff99cb500330c6767;
status reports active=false. Production changed: yes (first fix only).

The previous header called the full unsent report every minute on every Layout.
The follow-up uses GET /unsent-summary: authenticated owner actor lookup plus
one indexed settings read with a compact projection. No order/invoice scans,
provider requests, queue work, or cache refill on a miss. Owner UI polls every
five minutes, skips hidden tabs, prevents overlap and aborts on unmount.
Employees do not render or request the banner; server rejects employee actors
including native owner-shaped sessions. Full explicitly requested reports reuse
existing proof to cache a summary, bound to the pre-audit sync generation and
orders owner. Incomplete, stale, different-owner or superseded evidence is unknown.
No production load generator or worker changes were run.

Validation: affected backend suites plus reconciliation and seven new summary
checks pass (89 tests total); actual Jest passes six tests, including mounted
employee zero requests, owner interval/hidden tab behavior and request overlap.
A 20,000-proof fixture with 100 summary reads demonstrates one settings read per
call and <300-byte JSON payload, no order collection or provider access. The
unrelated iter164 accounting integration suite requires a local Mongo service and
was not run (collection failed due to absent MONGO_URL); no production DB used.
Next: checkpoint source A, governed A/B build/freeze, intent-only B, clean server
adapter rehearsal, CI/review, merge and publish under a fresh lease; verify3.

Known operational limits: live owner session reports qoyod_security_owner_required
on provider reconciliation; actual provider-verified unsent count is unknown.
Existing automatic sender logs show CandidateAuditScanLimitExceeded, a separate
worker issue. Do not claim successful sending or increase scan limits in this patch.
Issue #1006 has reached GitHub's comment limit; task PR conversation is the ledger.

## Prior checkpoint history


Follow-up compatibility checkpoint: PR #1762 merged as
a67da7816f24befb09fb713b294dea70e9517100 and its first publish is running.
Keep evidence gathering observational: invalid/repeated pages invalidate the
absence proof without shortening legacy pagination or changing returned rows.
The old sync wrapper uses fetched row counts for recovery decisions; changing
those counts would be an unintended sender-side effect. Regression coverage
now checks identical rows and page calls with and without evidence collection.
Follow-up branch: fix/qoyod-sync-proof-compatibility-20261105.
All 81 affected backend tests pass. Rebuild/freeze a new A/B release from the
merged base after this checkpoint, then publish under a fresh lease only after
the first release finishes and its existing lease is verified/closed.

State: remotely checkpointed in PR #1762; user authorized merge and publication.
Initial verified source: 5ea8e4d0ff6320616659f7464ea2c279699191b6.
GitHub Issue #1006 rejected further comments (2500-comment limit); PR #1762
is the continuation ledger for this task.

Base: d80661fd27ef88bf639fa43d8f969f4d62239a61.
Branch: fix/qoyod-truthful-unsent-banner-20261105.

The banner previously treated missing local mirrored invoice references as proof
that an order was not sent. The exceptions wrapper also filtered after limiting
rows, so its list could disagree with its count.

The candidate records tenant-scoped completeness evidence during invoice sync.
The banner and its linked list share verified absence references, require a
complete refresh less than 15 minutes old, exclude existing invoices regardless
of payment status, and classify unresolved local markers as unknown. Failed,
malformed, capped or repeated pagination cannot establish absence. Same-day
orders remain unknown because current audit proofs only expose a date. The
existing bounded audit remains bounded; truncated scans cannot establish totals.
Sender eligibility, financial writes and retry rules are unchanged.

Validation: 80 backend tests passed across verified-unsent, memory bounds, sync
identity, unified backlog, range counts, report eligibility and 404 pagination
suites (fresh run: 80 passed, exit 0). The actual Jest run passed all three
frontend tests. Both JSX files parsed; git diff --check passed. Source A's two
clean frontend builds matched (14.45 seconds); all 10 returned PR workflows
passed. Rebuild after this checkpoint, freeze intent-only B and rehearse the
adapter before publication.

Production changed: no. No production deployment or Release Guard lease was
created for this patch. The live reconciliation page reports
qoyod_security_owner_required; the actual provider-verified count is unknown.

Next safe action: complete the governed source/intent release and deployment
under the user's explicit merge/publication authorization. After deployment, an authorized
owner refresh must establish complete evidence; verify the banner and linked
list agree and an existing unpaid invoice is excluded. Never substitute 271 or
zero when evidence is unavailable. Prior operational recovery is separate from
this read-model fix.
