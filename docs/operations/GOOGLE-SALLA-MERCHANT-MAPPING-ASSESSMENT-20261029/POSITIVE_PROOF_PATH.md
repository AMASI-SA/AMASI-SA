# Positive Salla → Merchant proof path

2026-10-30 follow-up to `213e47fc46a4e1d00e055e7c657110a6bb2ed930`.

**Decision:** defer the earlier candidate-only UI. First identify the actual publisher and obtain its parent/variant identity contract. A positive proof is feasible when a trusted publisher record explicitly binds native Salla IDs to an exact Google product identity and the server reads back both ends. The public Salla material reviewed establishes an official feed publisher, but does not specify that cross-ID contract. Do not implement an assumed `offerId = Salla ID`, SKU, title, item-group or URL mapping.

## Confirmed sources and missing link

| Source | What is established | What it does not establish |
|---|---|---|
| [Salla's official app](https://apps.salla.sa/en/app/1916036203) | The public Google Merchant Center app is published by Salla. | Whether this owner installed it or uses its feed. |
| [Salla setup guide](https://help.salla.sa/en/article/google-merchant-center-integration/v25sqpe3tj6f99g3y5fl6p8b) | Its app settings provide a feed link; the merchant supplies that link to Merchant Center's file import. | Feed row schema, `id` derivation, variant-to-parent semantics, or a versioned mapping API. |
| [Salla feed guide](https://help.salla.sa/article/أساليب-لعرض-منتجاتك-على-google-بدون-تكلفة/ia968rqbss29z4lqjqpgxy6v) and [FAQ](https://help.salla.sa/article/أسئلة-شائعة-حول-مركز-جوجل-للبيع-بالتجزئة-google-merchant-center/g2yib4kylipzp838nanmbm4r) | Scheduled file delivery is documented; the FAQ recommends separate variant listings. | No guarantee that every installed feed uses a particular parent/variant identifier format. A recommendation is not an executable schema. |
| [Merchant data source reads](https://developers.google.com/merchant/api/guides/data-sources/view-sources) | Native GET/list identifies the source resource owned by the selected Merchant account, including its input kind. Advanced-account listing does not enumerate subaccount sources. | Publisher identity from a user-editable display name; Salla identity from a feed's existence. |
| [DataSource schema](https://developers.google.com/merchant/api/reference/rest/datasources_v1/accounts.dataSources) | Primary source, feed/language restrictions, file input configuration and default source precedence are explicit. FILE input can also receive API data. | An exact file-only ingestion lineage or unchanged attributes: supplemental/default rules can override primary attributes. |
| [Latest file upload](https://developers.google.com/merchant/api/reference/rest/datasources_v1/accounts.dataSources.fileUploads) | Processing state, timestamp and counts for a file source. | No file-byte digest or row-level Salla mapping is exposed by this schema; SUCCEEDED may still include item errors. |

These are bounded findings from official documentation and inspected code, not an assertion that no private publisher contract exists. No live provider/store settings, feed URL or credential values were read.

A generic [Salla App Setting Details GET](https://docs.salla.dev/5401096e0) exists, but its settings are developer-defined and its documented example includes a password field. It does not document the Google app's fields or establish that Mezan can read another app's settings. **Do not probe or dump it to discover a feed.** No access or schema is presumed from the public app ID. The available Salla connector tool metadata exposes product/variant/store reads, not a documented feed-publisher identity operation; no connector call was made.

## Repository provenance check

Exact additional blobs S27–S34 are recorded in [STATUS.json](STATUS.json), all at production baseline `622c310d8dcee13da61410bca6b3ac85134a9cfe`:

- `google_discovery.py` and `google_projection.py` discover/project Merchant accounts, permissions and API provenance. They do not persist data-source IDs, publisher/version, feed row bindings or export receipts.
- `salla_integration/service.py` exposes store ID/domain/scope/install mode. `config_store.py` is OAuth configuration, not a feed registry; no stored configuration values were inspected.
- `easy_mode_webhook.py` handles this app's installation/authorization lifecycle; its handler map has no feed identity or `app.settings.updated` mapping ingestion.
- `product_intelligence_foundation.py` declares feed-related signal/action categories, with publishing disabled. Those declarations are not a feed writer or export ledger.
- The old taxonomy CSV path (S19) emits only offer ID/category pairs using heuristics; Merchant registration (S24) projects account registration, not products. Neither supplies authoritative parent/variant mapping.
- Accepted Merchant source `3bbe1124…` exposes `Product.dataSource` (S07), but its bound transport permits the fixed products-list GET only (S09). Data-source reads would require an explicitly bounded new capability; do not bypass that allowlist.

Repository state cannot reveal which publisher the owner actually selected without a scoped native observation or owner input. No live database inspection was performed.

## Exact non-secret owner input

The first question should be limited to:

> ما اسم التطبيق الذي يرسل منتجات المتجر إلى Merchant Center، ورابطه في متجر تطبيقات سلة؟ وفي Merchant Center، ما رقم الحساب ومعرّف مصدر البيانات **الأساسي** لهذه المنتجات (أو اسمه إن لم يظهر المعرّف)؟ لا ترسل رمز وصول أو كلمة مرور أو رابط خلاصة يحتوي رمزًا سريًا.

Native owned-account discovery can supply the account/source choices later instead of asking the owner to transcribe IDs. A source name is a locator only; resolve it to its exact native resource and reject ambiguous names. If multiple publishers are present, select one source explicitly.

After the publisher is identified, obtain **one publisher-authored schema/version or support statement** specifying how an exported row identifies the native Salla parent and, when applicable, variant; plus a sanitized example containing one simple product and two variants of one parent. Required fields are the explicit source IDs, exported offer ID, row/group relation and full feed/language/source scope. Samples test the contract; numerical coincidence in samples does not create one. No support message is sent by this task.

For Salla's official app, the precise support question is: “What is the documented meaning and derivation of the feed's product `id` and `item_group_id`; how does a row bind to Merchant Admin API product/variant IDs, and can an authenticated read/export return that binding?” For a third-party publisher, request its equivalent read-only mapping/export API. If neither exists, existing remote offers remain unproved; further UI cannot close that evidence gap.

## Smallest implementation that can return a positive mapping

Implement a **backend proof service**, not another selection UI, after the publisher contract is available. The following is an internal normalized evidence model, not a claimed Salla response schema:

```text
PublisherBindingEvidence
  publisher_id + reviewed_contract_version + authenticated_store_scope
  source_record_id + source_revision/digest + observation_time
  salla_product_id + optional salla_variant_id
  merchant_id + native_product_resource + language + feed_label + offer_id
  primary_data_source_resource + binding_validity/retirement
```

A new publisher-specific reader constructs this model from trusted server reads or a publisher-authenticated export. The browser cannot submit an already-verified object. A bare file hash proves byte integrity, not issuer authenticity. An owner-entered pair remains a declaration. Unknown publisher versions, undocumented fields and retired/conflicting records fail closed.

`verify_publisher_mapping(binding, native_salla, native_merchant, source)` returns `mapping_verified:true` only when:

1. Saved-plan revision/full fields, current owner, store, exclusions and credential/account fences hold before and after the read; at most five explicitly selected native products are considered.
2. The reviewed publisher record explicitly names the current owned store and exact Salla parent/variant. A current authenticated Salla read confirms the same native identities and parent-child relation. Parent-only evidence cannot prove a variant; shared URLs and raw option labels cannot fill it.
3. A fresh bound Merchant observation contains the exact full native identity and exact primary data-source resource; a fixed native DataSource GET confirms ownership/type/scope. Match complete identities, not a collapsed offer ID. Do not infer Ads channel from `legacyLocal`.
4. The binding is current under the publisher's documented revision/validity semantics, with no contradictory source record. Unknown source-rule effects on identity or unsupported publisher transformations prevent acceptance. Non-atomic observations remain explicit; no proof of an exact Google ingestion byte snapshot is claimed.

Positive result scope: **observed publisher-backed cross-provider identity**, with evidence references and source times. It does not grant Google campaign eligibility, exclusive destination, stock/size coverage, fulfilment, profitability or execution. Hidden/excluded products remain blocked from selection even if their historical identity is proved. Keep provider/account identity and any later currency/time-window data separate.

Suggested isolated files: publisher adapter, pure mapping-proof validator, owner-scoped proof reader/route, focused tests. Attach to existing retail review only after backend acceptance. No collector changes, provider writes, feed creation, “fetch now,” campaign mutation, forced OAuth, new financial rights or UI work in the first slice.

A preceding **source discovery helper** can be implemented without knowing the publisher schema, but must be described as prerequisite provenance, not the requested final mapping feature. Permit only fixed Merchant `dataSources.get/list` and `fileUploads/latest` GETs under the existing bound owner/credential checks. Request an explicit [response field mask](https://docs.cloud.google.com/apis/docs/system-parameters) for resource/type/feed/language/rule metadata and upload state; omit `fetchSettings`, username/password and raw upstream error bodies. No arbitrary fetch URI is followed. Test mask handling against sanitized native fixtures before use. Safe metadata can establish a positive native source-ownership fact; it does not identify the Salla publisher by itself.

## Alternative: an export record we own

If the actual publisher provides no authoritative binding, an owned local export manifest is the honest alternative, not a retrofit of guessed mappings onto existing offers. A bounded server-side manifest builder can bind native Salla parent/variant IDs, raw-source digests, owner/store scope and a reviewed serializer version to exact locally generated export rows. Preserve explicit parent/variant identity; do not use SKU uniqueness as identity. Initially use internal `export_row_id`, with Merchant offer/resource fields absent, so no invented native Google ID is presented.

This gives a positive **Salla→local export-row provenance** fact. A server-generated downloadable artifact can avoid new database namespace writes; a durable append-only export registry would need its own reviewed storage/index contract. Neither a hash nor a locally prepared artifact proves that Google has received it. A future separately authorized writer/import and exact native readback would be needed to establish a new Google mapping from that manifest. No such publish/import is authorized here. If an existing Mezan-owned historical export/receipt is later discovered, verify that exact receipt instead of fabricating one now.

## Acceptance tests before enabling positive results

| Case | Expected evidence |
|---|---|
| Real publisher fixture + actual HTTP saved-plan writer + current native parent | Positive parent mapping only; exact source/full Merchant key. |
| Real variant fixture | Positive only for the explicit parent/variant relation; siblings cannot inherit it. |
| SKU/title/URL/numeric coincidence, self-authored owner file, wrong issuer/version | No positive mapping, even when all strings happen to match. |
| Duplicate offers across source/account/feed/language/channel, shared parent URLs, conflicting/retired bindings | No collapsed identity or inferred winner. |
| Source replacement, supplemental transformation, incomplete traversal, unknown validity or read races | Refusal or explicit missing proof; no stale positive response. |
| Data-source transport | Fixed GETs and explicit field masks; deny credential fields, foreign resources, redirects and arbitrary feed URLs; bounded bytes/pages/deadlines. |
| Real Mongo owner/store/plan/credential changes | Before/after guards and zero-skip CI; real writer fields omit write-only controls. |
| Local manifest alternative | Deterministic rows/digests, explicit parent/variant source; local provenance positive while Google delivery/mapping remains unclaimed. |

No new executable implementation or test suite was added by this follow-up. Validation is official-document/source inspection, exact blob/parity checks and documentation consistency. The earlier ten synthetic probes remain historical evidence for unchanged functions. The immediate dependency is publisher/source identification; the smallest positive implementation is then one reviewed publisher adapter plus the pure proof verifier, not a general all-platform mapping service.
