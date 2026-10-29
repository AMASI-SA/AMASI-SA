# Salla ↔ Merchant identity and campaign coverage

Decision: implement a bounded **candidate-evidence review**, then a separately reviewed identity-proof adapter. The available readers can establish each platform's native identity and the Ads↔Merchant account relationship. They do not establish that a Merchant offer represents a particular Salla parent or variant. SKU, title, numeric-ID coincidence, an owner-selected pair, or a shared URL must not acquire that authority.

This is a documentation-only assessment of production `622c310d8dcee13da61410bca6b3ac85134a9cfe` plus accepted Merchant source `3bbe11247b10c69a610b88bc87ba008e674924e4`. It is not a deployment, live-provider inspection, account-eligibility verdict, or authorization to change a feed/campaign/budget. Source identifiers below resolve to exact commit/path/blob records in [STATUS.json](STATUS.json). The accepted Merchant release is independently managed in PR1658; no additional release blocker is asserted here.

## What the inspected sources establish

| Evidence | Positive fact | Remaining boundary | Source |
|---|---|---|---|
| V2 catalog | Owned Salla product ID, local raw payload digest and sync timestamp | Refresh can retain old variant arrays; normalization supplies defaults. Neither local recency nor a normalized currency/stock value proves native completeness or a feed binding. | S01–S02 |
| Native category + inventory reads | Owned category endpoint membership; selected parent ID and HTTPS customer URL; bounded variant IDs, source option labels and raw stock observations | Parent stock is separate from variant stock. Partial pages, unknown unlimited quantity and unknown category visibility remain explicit. No variant destination URL or Merchant identity is exposed. | S03–S06 |
| Merchant observation | Full processed-product identity `(merchant, legacy_local, language, feed_label, offer_id)`, resource/base64 name, primary data source, links, item-group ID, version and archive metadata | Complete traversal is non-atomic. A primary data-source ID or item-group ID does not identify a Salla store/product/variant. `legacy_local:false` does not establish the Ads ONLINE channel. Links are observed strings, not fetched destinations. | S07 |
| Ads↔Merchant binding | Owned local selections plus native Ads product-link reads bracketed by principal/account/credential checks | Proves an account relationship, not store-product identity or uninterrupted provider authorization. | S08–S09 |
| Native Ads product selector | Consistent full ShoppingProduct resource, explicit merchant/feed/language/channel, account-scope eligibility and availability | Rejects duplicate offer IDs even across other feeds/languages and even when a competing row is ineligible. Successful selection still says `owner_selected_unverified`; account scope is not campaign delivery. | S10–S12 |
| Manual/AI campaign selections | Explicit owner choice validated against the owned catalog and native Google inventory | `product_id + merchant_id + offer_id` is an intended association, not independently verified mapping. Existing campaign-product events also represent intended scope. | S13–S15, S18 |
| Connector catalog | Distinguishes API connection, legacy integration and data-feed provenance; explicit permissions/capabilities | A fresh feed, connected badge or diagnostics capability does not confer product identity or advertising-write authority. | S16–S17 |

The inspected raw catalog writer, native readers, Merchant observation, registration, association models and repository searches did not reveal a trusted Salla→Merchant export ledger. This is a bounded repository finding, not a claim about uninspected external Salla applications or the owner's actual feed configuration. The registration module explicitly does not manage products (S24).

The older taxonomy CSV helper (S19) indexes only `offerId`, tries SKU then Salla ID and then case-folded equality. It collapses feed/language identities and selects the first catalog row for duplicate SKUs. Ten source-function probes passed their expected assertions: five demonstrate these limitations, and five confirm the conservative native selector's existing behavior. [PROBES.json](PROBES.json) records synthetic inputs/results and method limits. No real product, native API or database was queried. Do not reuse the CSV matcher as mapping proof or silently change it in this slice.

## Identity and destination rules

Preserve every native identifier literally. Merchant API identity includes language/feed/offer within the account; Ads ShoppingProduct additionally has its own customer/channel resource identity. Google's product schema does not assign Salla meaning to `offerId`. Its primary data source describes Google-side provenance only ([G1](https://developers.google.com/merchant/api/reference/rest/products_v1/accounts.products)). Retain native Ads channel instead of deriving it from Merchant `legacyLocal`.

Salla's Merchant Admin API documents separate product and variant reads: parent ID/customer URL and variant ID/SKU/option references are distinct fields. These are not interchangeable identities ([Salla product](https://docs.salla.dev/product/product-details), [Salla variants](https://docs.salla.dev/product-variants/list)). A parent URL may be shared by many variants. Identical URLs among five selected products do not prove uniqueness across the store or feed. Keep query, fragment, scheme and port; no case-folding paths, stripping tracking parameters, slug parsing, redirects or arbitrary URL fetching. Report exact equality separately for `link`, `canonicalLink`, `mobileLink` and `adsRedirect`; none proves the eventual served destination or variant identity.

Archived Merchant products, hidden/out/unknown Salla products, exclusions and unresolved exclusion IDs remain visible reasons against selection. An identity may be observed while eligibility is unproved. A hidden category or partial category traversal cannot become a complete feed selection. Native Ads ELIGIBLE/ELIGIBLE_LIMITED + IN_STOCK is a scoped observation, not Salla fulfilment, preparation time, profitability or launch permission. Google documents query-scoped status and potential update delay of up to 24 hours ([G2, v25](https://developers.google.com/google-ads/api/reference/rpc/v25/ShoppingProduct)).

## Campaign coverage is three separate contracts

| Campaign surface | Implemented source behavior | What must remain explicit |
|---|---|---|
| Search | Manual RSA/DSA builders; automatic standing-authority creation contract accepts SEARCH/RSA only. | A category/product URL is a destination input, not Merchant membership. Search automation does not cover every campaign type. |
| Standard Shopping | Manual native shopping settings and product-item listing tree; selected IDs exclude the Other branch. Empty generic selection includes the feed root. | Product-specific store selections require explicit native offer choices. Identity, feed/link, campaign validation and financial approval are separate gates. |
| Retail Performance Max | Manual shopping settings, asset groups, final URLs and native listing filters. | Assets and listing membership are not proof of exclusive destinations. Current builder configures URL-expansion automation only in its Search branch; it does not explicitly opt PMax out. |
| Other types | Capability table declares separate Display, Demand Gen, App and Hotel builders, with type-specific formats/prerequisites; other types have explicit limitations. | This assessment neither certifies live account eligibility nor extends automatic creation beyond Search. Reuse neither Search authority nor product-report scope as universal coverage. |

Source: S13, S20–S23. These are implementation claims, not successful native campaign creation. Current PMax documentation says URL expansion defaults on; the v22+ control is `FINAL_URL_EXPANSION_TEXT_ASSET_AUTOMATION` ([G3](https://developers.google.com/google-ads/api/docs/assets/asset-automation-settings), [G4](https://developers.google.com/google-ads/api/performance-max/optimizations)). **Inference:** supplying a category/product final URL in the current builder cannot promise that only that URL serves. A future exclusive-destination feature needs an explicit PMax control, native readback and tests; do not silently toggle an existing campaign.

Google's reporting guide separates current product state from historical performance. Account-level ShoppingProduct status covers Shopping/PMax; campaign scopes have different supported types/fields. Historical product performance is not an independently attributed Salla order ([G5](https://developers.google.com/google-ads/api/docs/shopping-ads/reporting)). Any later metrics must retain provider, Ads account, Merchant/full product identity, campaign scope, native currency, native timezone/date, window and attribution definition; never sum repeated contexts across products/windows. The proposed slice returns no metrics or prices and invents no currency.

## Smallest callable contract: proposed, not implemented

Add an owner-triggered read-only request, using server-saved business fields rather than write acknowledgement fields:

```http
POST /google_ads/commercial-plans/{plan_id}/merchant-mapping-review
```

```json
{
  "expected_revision": 3,
  "category_id": "123",
  "selected_product_ids": ["456"],
  "account_id": "789",
  "merchant_id": "321"
}
```

All IDs above are fictional examples, not observed account/product IDs. Reject extra fields, duplicates, noncanonical IDs, more than five products and any client-supplied verification flag. Require current authenticated owner, saved revision/full-field equality, one saved category (including exact saved-URL resolution), exclusions resolved, current owned store binding and owned Ads/Merchant selections. Selected products must be observed in a fresh native category reproof. Category traversal may remain partial; it never establishes coverage of unobserved products.

Use the accepted native inventory and credential-bound Merchant observation services, not browser-returned evidence. Reuse their actual-writer schema and before/after guards. Extract a common read service only if needed; do not bypass an HTTP route's security checks by calling an inner helper alone. Recheck owner, full plan, store connection and both Google account/credential bindings before publishing the joined result. An unrelated plan edit also invalidates it. Permission failure stays endpoint-specific; do not force OAuth relinking from missing stored scope declarations.

Proposed bounds: five Salla products, existing category/variant bounds, Merchant 10 pages/2,000 products/2 MiB per page/8 MiB accepted total, at most 200 returned candidate edges and 512 KiB serialized response. Keep existing per-stage deadlines (45s Salla, 115s Merchant route) inside a 170s outer deadline; cancel children on timeout/disconnect. This is an implementation target requiring tests, not a measured latency or peak-memory claim. If candidate output truncates, set `candidate_output_complete:false`; no-match or uniqueness must not be claimed for the omitted portion. Merchant incomplete traversal fails closed under its existing contract. No candidate auto-selection and no persistence/provider mutation.

Return native contexts once with references from product rows. Include source digests, each source's own start/finish time, native IDs/URLs, pagination and archive/status facts, plus:

```json
{
  "status": "candidate_evidence_only",
  "plan_revision": 3,
  "mapping_verified": false,
  "missing_proof": ["trusted_store_to_merchant_identity_binding"],
  "destination_verified": false,
  "campaign_eligibility_verified": false,
  "execution_authorized": false,
  "remote_snapshot_atomic": false,
  "provider_writes": 0
}
```

Candidate reasons may describe exact native URL-string equality; SKU/title are display context only. Report multiple matches and absent URLs without choosing a winner. Preserve each identity and all archive/visibility/exclusion reasons. Existing native stock remains observations only. Do not add an Ads inventory query to this first slice: the Merchant observation correctly leaves channel and Ads eligibility unproved. A later bounded native Ads query can join exact Google identity components and expose **account-scoped** eligibility separately; it still cannot establish Salla identity.

A child UI should say “مقارنة هويات وروابط مرصودة — ربط سلة بعرض Google غير مثبت”, require an explicit owner button, and cancel/discard on owner/account/category/product selection changes, dirty/save/revision/full-field changes and unmount. No GET/POST on mount, no save/selection side effect and no readiness/success badge implying advertising authority.

## What can produce a positive cross-provider proof

A future adapter needs a trusted provider/feed-writer record that explicitly binds `owner + store + Salla parent + optional variant` to `Merchant account + full native product identity + primary data source`. Require provenance, source revision/digest, binding event/version, validity/retirement and source observation times. Read back the exact processed Merchant product and the corresponding Salla native identity. Reject contradictory bindings, variant/parent substitution, retired mappings and unknown source ownership. An owner declaration or merchant-editable custom attribute by itself remains an assertion; a URL/SKU match is not a substitute for the missing record.

First discover whether the store's actual feed publisher exposes such an authoritative read-only export/manifest or API binding. Its provider-native contract and ownership must be reviewed before choosing a schema. No new feed writer, identifier fabrication, backfill, schema migration or paid/provider action is authorized by this assessment. Even a proved identity leaves stock, destination, campaign scope and launch approval as separate facts.

## Required implementation evidence

| Area | Minimum cases |
|---|---|
| Reachable writer contract | Actual HTTP owner PUT → read; saved fields omit `acknowledge_review`/`expected_revision`; nullable category URL; saved URL-only and category-ID scope; prior context preserved. |
| Ownership/races | Real Mongo owner/viewer/other owner, revision/full-field changes, credential/account/store rebinding, principal revocation, saved exclusions and unresolved exclusions. No provider call before initial refusal. |
| Identity ambiguity | Same SKU/title, case differences, numeric Salla/offer coincidence, same offer across merchant/feed/language/channel, duplicate URLs, shared parent URL across variants, archived or contradictory records. No positive mapping. |
| Bounds/transport | Merchant duplicate identity, incomplete/oversize pages, Salla partial category/variant pages, timeout/cancellation and output cap. No arbitrary URL/redirect fetch; query/fragment preserved. |
| Scope/eligibility | Hidden/out/unknown and zero/unlimited-unknown remain distinct; no variant stock from parent; `legacyLocal:false` never becomes ONLINE; native Ads status cannot become campaign readiness. |
| Actual parent UI | Real AuthProvider plus generated actual-writer fixtures; explicit request only; dirty/save/owner/account/category/selection changes and unmount discard late results. No auto-selection or plan save. |
| Regression | Preserve accepted Merchant, native/linked/current/prior observation tests, real-Mongo zero-skip gates, actual router/parent integration and full backend/UI/build. New fresh production source required before any release. |

Assessment validation: ten synthetic source-function assertions; exact source blob manifest; JSON parse and docs-only diff checks. No new runtime tests, full CI, live provider proof, host action or deployment are claimed. The proposed callable slice requires its own implementation, independent review and fresh CI.
