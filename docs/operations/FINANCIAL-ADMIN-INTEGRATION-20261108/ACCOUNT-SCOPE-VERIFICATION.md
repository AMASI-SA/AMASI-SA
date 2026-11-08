# Assigned account read boundary

The owner retains consolidated financial reporting. Staff report-read permission
continues to authorize the standalone daily page and assigned bank/cash account
statements. It does not authorize company-wide balances containing hidden accounts.

## Routes

- `/reports`, `/financial-position`, `/cost-exposure`, and `/audit`: verified owner
  only, with an explicit 403 explanation for scoped readers. No reduced or
  fabricated company-wide totals are returned.
- `/accounts/{bank|cash}/{id}`: assignment checked before account lookup, then
  live permission and assignment checked before returning. Both transfer directions
  retain their exact authorized monetary leg; unassigned counterpart names,
  references, and notes are redacted.
- `/movements`: assigned source legs only; unassigned transfer counterpart fields
  are redacted. Browser movement-read and native report-read remain separate.
- Bank/cash entity lists also apply assignments to browser staff.
- `/context` retains `permissions.reports` for authorized daily/account reads and
  adds `can_view_consolidated_reports`. Restricted readers do not receive company
  engine issues or counts of hidden account openings.
- Collection-review queue filters assigned bank accounts before its limit/count.
  Options, receipt downloads, operational/native approvals and subsequent POS bank
  settlements apply account scope. Transaction checks include fresh external
  assignment reads, so revocation during approval rolls back the financial effect.
  Driver-owned receipt access and current driver payloads are unchanged.

## Verification

Dedicated local replica set `accountScope`, port 27374, disposable databases:
108 tests passed in 22.91 seconds; process stopped after tests.

The selection included daily totals/permissions, native app permission matrix,
entry/account statements, supplier-adjustment permissions, general security,
request ownership, and operational driver-review app transactions. It covers
unassigned bank rejection, hidden transfer details in both directions, account
assignment revocation during reads, bank assignment revocation during approval,
read-only versus write authority, independent daily access, and idempotent approvals.

An earlier dedicated replica run passed 123 operational/native collection-review,
POS settlement and daily permission tests. No production records or financial
amounts were modified by these boundary changes or their tests.
