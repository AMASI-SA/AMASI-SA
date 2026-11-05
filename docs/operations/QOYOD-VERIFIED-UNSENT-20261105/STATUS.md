# QOYOD-VERIFIED-UNSENT-20261105

State: verified local candidate; not merged or deployed.

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
suites. Three frontend helper tests passed using a Node assertion harness; both
modified JSX files parsed with Babel. git diff --check passed. A complete
frontend release build and independent release review remain outstanding.

Production changed: no. No production deployment or Release Guard lease was
created for this patch. The live reconciliation page reports
qoyod_security_owner_required; the actual provider-verified count is unknown.

Next safe action: review the draft diff and CI, then prepare the governed release
only after separate deployment authorization. After deployment, an authorized
owner refresh must establish complete evidence; verify the banner and linked
list agree and an existing unpaid invoice is excluded. Never substitute 271 or
zero when evidence is unavailable. Prior operational recovery is separate from
this read-model fix.
