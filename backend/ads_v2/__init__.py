"""Ads V2 — Phase 0 module.

Strict isolation from V1:
- Reads V1 OAuth/connection collections ONLY (snapchat_connections,
  meta_connections, tiktok_connections) — never writes/modifies them.
- Owns its own collections prefixed `ads_` (the simplified 4-collection
  design): ads_accounts, ads_daily, ads_sync_logs.
- The general_ledger remains the global SSOT — Phase 0 does NOT write
  to it.
"""
