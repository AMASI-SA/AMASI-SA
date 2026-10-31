# Native Merchant source provenance (unattached)

This slice discovers the native account-scoped source resource, its kind, input method and optional feed/language pair. It does not identify the publishing application or map a Salla product/variant to an offer. Source ownership, SKU, title and offer ID are not mapping proof. The remaining positive mapping input is publisher-authored provenance or an authorized export/readback ledger; the earlier mapping assessment remains applicable.

The caller explicitly supplies one currently saved Ads account and one currently saved Merchant account. The accepted authenticated-owner snapshot, connection/account snapshots, encrypted-credential fence and complete native Ads-to-Merchant link checks bracket the read. This intentionally retains the existing content + adwords capability requirement. It cannot discover a standalone Merchant account through a relaxed permission path.

The opaque bound session makes only `GET https://merchantapi.googleapis.com/datasources/v1/accounts/{canonicalMerchantId}/dataSources`, with one fixed response mask and page size 100. There is no caller URL, method, fields, page-size or account-path override. It follows no redirects or returned URLs. The resource-level `get` method is unnecessary for this minimal slice. Fetch settings, filenames, URLs, passwords, labels and source rules are neither requested nor accepted by the decoder.

The traversal allows at most five pages/500 sources, 256 KiB per accepted page, 1 MiB accepted total, 10 seconds per page and 45 seconds overall. These are streaming input bounds, not an exact peak-memory claim. Any incomplete traversal, duplicate identity, page-token cycle, schema drift, permission failure or changed authority discards the result. Successful empty enumeration is distinguished from failed evidence. Successful traversal remains non-atomic; publisher identity, product mapping, ad eligibility and execution authority remain unverified.

The new route factory is deliberately unattached. Proposed later parent seam, only after review: import `attach_google_merchant_data_sources_routes` and call it with the existing router/database/current-user dependencies. GET path: `/google_ads/merchant-products/data-sources?account_id=...&merchant_id=...`. No UI attachment is in scope.

Primary documentation inspected 2026-10-30:

- [DataSources v1 list](https://developers.google.com/merchant/api/reference/rest/datasources_v1/accounts.dataSources/list): canonical account parent, pagination and content OAuth scope.
- [DataSource resource](https://developers.google.com/merchant/api/reference/rest/datasources_v1/accounts.dataSources): resource ID, source union and input enums. A file source can also accept API inputs; input type does not prove an exclusive publisher.
- [Google API system parameters](https://docs.cloud.google.com/apis/docs/system-parameters): `fields` response filtering. The server response is still strictly validated; a mask is not assumed to eliminate unexpected content.

No live provider request or secret/configuration inspection was performed. Local tests use synthetic credentials and intercepted native requests. The completed local set has 438 passing tests, including 140 new cases and 298 preserved owner/product/transport/reporting/refresh cases, with warnings as errors and no skips. A local inherited SOCKS proxy initially blocked 14 old reporting tests at client construction; unsetting proxy variables for this synthetic run resolved the harness issue without changing dependencies or runtime.

The new workflow runs 504 tests and asserts exact counts and zero skips: 14 new actual Mongo cases plus the preserved 21 owner-snapshot, 10 bound-session and 21 refresh-CAS Mongo cases. Native responses are intercepted even in real Mongo tests. CI evidence and independent review remain pending until recorded separately.

The 20-line transport seam is additive; the original product-list method, credential acquisition/refresh, local snapshot gates and all existing files other than `google_ads_reporting.py` remain unchanged. New tests cover fixed request masking, forbidden path/query overrides, malformed identities, duplicate pages, partial traversal, source schema drift, sensitive unexpected fields, byte/deadline limits, cancellation, authority changes and unmodified persisted documents. Actual deployment and provider capability are not established by these tests.
