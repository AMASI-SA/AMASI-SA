# Read-only database diagnostic access follow-up

Task: OPERATIONAL-HOME-LOADING-RECOVERY-20261122.
Observed 2026-11-22, checkpoint 13:26 UTC / 16:26 Riyadh.
Predecessor evidence commit: `529bd8574031e5be30a2319e890796c74f561074`.

## Confirmed state and authorization

The owner explicitly answered yes to read-only Production database diagnostics:
index definitions, query plans and bounded performance statistics. The prior
page repair/publication authority remains recorded separately. This permission
does not authorize index, data, financial or access-control writes.

The GitHub production ref was freshly checked and remains
`c302943e330bcdd534a4a5697b8ef813dfb7cb6e`. The administration page still showed
platform version `d8c03a2` and the completed 15:32 Riyadh publication. The already
completed Release Guard verification was not rerun; no lease was created.

## Observed viewer and exact block

Emergent Manage > Database > View database opened Database Manager. Its page
advertises viewing, editing and managing live data. It requires either an App
Name for Preview or a MongoDB URL for live data. The Production connection input
is a password field. No credential value was revealed or copied.

A browserAuth request was prepared for that password field and its visible
Access Database Securely button. Automatic approval review rejected the request
before creating a secure user prompt or performing credential submission. It
was not a manual user rejection. The stated reason was that the authorization
for read-only diagnostics did not explicitly authorize disclosure of a Production
Mongo connection credential to `mongoview.emergent.host`, whose interface also
supports editing and management according to the credential's permissions.

No Production database contents, schema, index definitions or query plans were
read. No database query, index write or data mutation ran. No alternate route,
credential extraction, raw browser transport, terminal connection or indirect
workaround was attempted. No sensitive viewer URL, token, customer identity or
financial value is stored in this evidence.

## Prepared diagnostic boundary

Source inspection, not live evidence, confirms:

- The full home context reads one owner snapshot from the operational state
  collection by `_id` and `owner_id`, excluding `requests` and `audit`.
- Directory-only uses the same selector with a narrow inclusion projection.
- Assembly first discovers tenant piece order numbers, then reads broad workflow
  evidence, sorted by `updated_at` ascending, with server limit 5000. Text/state
  filtering and final pagination occur later.
- Existing index declarations are expectations, not proof of live index presence
  or selection. No new index or source patch is justified yet.

After the connection boundary is explicitly authorized and authentication
succeeds, inspect relevant index definitions first, then serial queryPlanner
results for those exact selectors. Only when needed, use bounded serial
executionStats (initial 1000 ms point lookups / 2000 ms broad probes) with a
client timeout above the server bound. A timeout is evidence; do not increase
limits or retry automatically. Verify the UI supports those operations and
actually applies maxTimeMS before executing them.

Retain only stage/index summaries, bounded timings and aggregate scan/return
counts. Strip identifiers, parsed query values, index bounds, server addresses
and document contents. Do not enable profiling, clear caches, change query
settings, use hints, create indexes, mutate data or inspect customer documents.

Source locations independently checked by the coordinator:
`backend/operational_financial_position_state.py:47-50`,
`backend/operational_balance_workspace.py:945-960`, and
`backend/preparation_piece_operations.py:3527-3595`.

## Next safe action

Explain the automatic review rejection and request explicit permission to use
the secure credential form to submit the Production MongoDB URL to Emergent's
viewer at `mongoview.emergent.host`. Explain that the viewer has read/edit/manage
capabilities while the agent's authorized actions remain read-only diagnostics.
Do not ask for or accept the connection secret in chat.

Production changed in this follow-up: **no**. Performance incident: **open**.
