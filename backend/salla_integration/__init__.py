"""Salla OAuth + Merchant API integration — Phase 1 (read-only connect).

This module is intentionally ISOLATED from the existing data sources
(Make.com webhooks, manual PDF upload, manual Excel upload). It exposes
its own router under `/api/salla/*` and writes only to the
`salla_integrations` collection. No other code path imports from this
module — by design — so we can pull the plug instantly if Phase 2
(webhooks + sync) reveals any regression.

Phases
------
1. (this file) OAuth Authorization-Code flow + encrypted token storage
   + auto-refresh wrapper + /store/info "test connection".
2. (NEXT)      Programmatic webhook registration + HMAC verification +
               POST /api/webhooks/salla/order persistence.
3. (LATER)     Sync historical orders + Salla↔system reconciliation tool.
"""

from .routes import attach_salla_routes, ensure_salla_indexes

__all__ = ["attach_salla_routes", "ensure_salla_indexes"]
