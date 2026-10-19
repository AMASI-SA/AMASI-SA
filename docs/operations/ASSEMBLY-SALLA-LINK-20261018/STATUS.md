# Assembly Salla link setting

Base: `e015b913ce950d6e5cf52cc0f1893da94b7b30b4` (fresh origin production branch).
Branch: `codex/assembly-salla-link-settings-20261018`.
Production changed: no. No merge, deployment, provider request or custody write.

## API

All paths use `/preparation-work-v1` (and deployment `/api` prefix).

- GET `/assembly/settings`: `{show_salla_order_link, can_manage}`.
- PATCH `/assembly/settings`: strict boolean `show_salla_order_link`; returns same fields.
- GET `/assembly/orders/{order_number}/salla-link`: `{show_salla_order_link, salla_admin_url}`.

Settings persist under the merchant settings document at
`assembly_shipping.show_salla_order_link`, default false. Reads require current
assembly app-page access. Writes additionally require the existing native
`app.role.manager` grant; merchant owners retain existing super-admin authority.
Native bridged owner identities are resolved back to the actual employee before
checking current access. No operational permission is added.

The link endpoint rechecks the setting at click time. It returns only the saved
provider admin URL on `https://s.salla.sa/orders/{internal-id}`, matching canonical
provider reference and internal ID. Missing or invalid URLs return null; customer
links and generated fallback URLs are never used. Other URL forms intentionally
fail closed. Clients hide/disable on fetch failure and do not open cached links.
No extra work is added to assembly search, ready or shipping paths.

## Verification

Isolated MongoDB plus synthetic users/orders, no live provider calls:
`python -m pytest backend/tests/test_assembly_mobile_settings.py backend/tests/test_mobile_app_manager_role.py backend/tests/test_preparation_piece_operations.py -q --asyncio-mode=auto`

64 passed in 4.68 seconds. Covers manager/staff distinction despite native owner
bridge, shared merchant state, other-merchant isolation, current revocation,
disabled account, immediate link suppression, strict input, URL identity/host,
unrelated settings preservation, and existing route contracts.

Native adapter tests call the real `mobile_app_request_user` with actual `/api`
paths and methods for all three endpoints, using current persisted employee grants.
They cover manager PATCH, staff reads, staff/revoked PATCH denial, foreign tenant
isolation and disabled owner authentication. They do not claim JWT transport E2E.
The existing server startup creates `db.settings` unique index on `user_id`
(`server.py:4696`); this feature uses its standard tenant update/upsert pattern,
adding no migration or alternate settings collection.

New Assembly Mobile Settings workflow runs focused auth/settings tests against
an isolated MongoDB service. Remote CI is pending Draft PR creation.
Next: independent review, exact-HEAD CI, coordinate Android callers. No release authorized here.

## CI setup correction

PR #1435 run 39140541760 failed before collection because shared test conftest
imports openpyxl. Added the native adapter's actual transitive import dependencies
to the focused workflow. A newly created local venv using exactly that install
list passed all 15 workflow tests in 7.42 seconds against isolated MongoDB.
Explicit tzdata also makes the clean Windows environment match Linux's system
timezone availability. No tests, permission guards, or assertions were weakened.
New exact-HEAD GitHub verification remains required.
