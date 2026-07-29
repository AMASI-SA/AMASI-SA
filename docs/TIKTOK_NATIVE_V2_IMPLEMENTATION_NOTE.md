# TikTok native V2 implementation boundary

This delivery establishes the direct TikTok Marketing API connection and
advertiser discovery boundary. It intentionally does not implement campaign or
reporting synchronization yet.

## Included

- owner-only OAuth start/callback
- signed one-time state and browser binding
- encrypted tenant-scoped access-token storage
- direct advertiser account discovery
- V2 account, permission, health, sync, and error projections
- TikTok card no longer reads Make or legacy TikTok collections
- app-level management permissions can be authorized for future use
- all campaign/ad/budget mutations remain approval-gated

## Next delivery

- direct Marketing API reporting sync
- raw and normalized V2 campaign/ad/creative facts
- bounded date windows and provider-call budgets
- reconciliation against Make during migration
- disable Make after successful reconciliation
- proposal/preview/approval/execution/verification/audit/rollback gateway
