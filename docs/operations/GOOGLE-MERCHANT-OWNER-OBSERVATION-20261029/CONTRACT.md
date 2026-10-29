# Owner-requested Merchant observation (unattached WIP)

Parent is reviewed observer/helper source `ca5648d4906a4cb0ab170998c1d28491921f43bc`.
Only new route module, its tests, dedicated workflow and this task's documents
are owned here. Root owns router attachment and production integration.

The proposed GET `/google_ads/merchant-products/observation` requires explicit
`account_id` and `merchant_id`; neither saved selection nor configuration is a
default. The existing authenticated principal must be the owner. Before any
provider access, a bounded PRIMARY snapshot transaction reads the current owner,
both integration rows and the complete local account sets for Google Ads and
Merchant Center. The selected rows must each be unique, connected and have
`api_connection` provenance. Ambiguous identities, duplicate rows, missing
providers, incomplete sets or unavailable snapshot transactions refuse the read.
Relevant projected BSON fields, including absence/null and document identity,
are compared again after credential acquisition and after provider reads.
No fallback, account selection update, connection repair or projection write.

Use only the reviewed opaque same-token session. Query all Merchant Center
`product_link` rows in the explicitly selected Ads account with a fixed GAQL
query, bounded row count and complete result requirement. Every returned row
must have consistent customer, native link resource, link ID, type and Merchant
ID; duplicates and anonymous rows refuse the whole observation. The selected
Merchant must have exactly one native link. Re-read the complete link set and
account metadata after bounded Merchant product traversal, and compare exact
semantic evidence. Do not return data until local membership and the helper's
final credential fence both pass. All transport exceptions are mapped to fixed
safe errors. No provider writes except an existing necessary CAS token refresh.

Read snapshots bracket the operation; they cannot prove uninterrupted state
between reads or exclude an external writer. Local account set completeness is
not completeness of historic OAuth discovery. The current native Ads link and
successful Merchant GET are separate observations. A connection may change
after the final check; the response never authorizes a later advertising action.
Merchant traversal remains non-atomic. No store/feed product mapping, variant,
category, landing destination, inventory, Ads eligibility or attribution claim.

Acceptance covers owner/staff/disabled boundaries; forged account/merchant and
ambiguous rows; missing/null projection changes; full set additions/removals;
revoke/relink during a paused provider read; native malformed/duplicate/partial
links and drift; no partial product escape; safe errors; cancellation; and real
Mongo replica snapshot races. Use actual v25 protobuf fixtures, and actual
existing helper/observer with synthetic transport and synthetic cryptography.

Primary sources checked 2026-10-29:

- https://developers.google.com/google-ads/api/reference/rpc/v25/ProductLink
- https://developers.google.com/google-ads/api/fields/v25/product_link
- https://developers.google.com/merchant/api/reference/rest/products_v1/accounts.products
- https://developers.google.com/merchant/api/reference/rest/products_v1/accounts.products/list

v25 ProductLink has no status field; it is distinct from an invitation. Resource
names are `customers/{customer_id}/productLinks/{product_link_id}`; the link ID
and Merchant ID are int64. All requested fields and `customer` attribution are
selectable. The installed Google Ads 33.0.0 v25 protobuf is the schema oracle for
fixture validation, not evidence of live provider access.
