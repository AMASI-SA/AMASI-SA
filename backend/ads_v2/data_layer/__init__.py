"""Ads V2 — Data Layer (Single Source of Truth boundary).

Phase 0 exposes only a handful of functions. As later phases land,
this layer grows but stays the ONLY place that reads/writes the
ads_v2 collections directly.

Boundary rules (enforced by tests/test_ads_v2_data_layer_boundary.py):
  • No file outside `/app/backend/ads_v2/data_layer/` may touch
    `db.ads_accounts`, `db.ads_daily`, `db.ads_sync_logs`, or
    `entry_type` starting with `ads_v2_`.
  • Exception: the lint test file itself, and the tests directory.
"""
