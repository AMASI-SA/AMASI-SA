# Cross-platform tracking diagnosis: implementation boundary

Owner authorization: implement read-only store pixel inspection, compare with
the selected advertising account/campaign, and give the campaign analysis actual
evidence. Platforms: TikTok, Snapchat, Meta, Google. Existing pixel/event sources
must not be replaced. No campaign, pixel, tag, budget, consent, or checkout writes.

## Current verified source

Base: `44b14c38cfe1a473829fc757a7b110f350b0ebb3` on
`hotfix/prod-snap-meta-final`. This is the latest observed remote branch head.
The last official production verification was PR1347, platform184a27a, on
2026-10-12. No fresh production identity verification was performed for this
new task and production was not changed.

Existing reusable components:

- `campaign_ai_public_page_probe_v3.py`: bounded HTML retrieval, no JavaScript
  execution or runtime event observation. Not a substitute for a browser check.
- `campaign_ai_product_intelligence_v3.py`: owner-scoped product associations and
  stored Salla canonical product URLs. Never trust an arbitrary model-supplied URL.
- `snapchat_native_tracking_diagnostics.py`: native read-only asset and event
  diagnostics. Reuse its scope/provenance; do not create a second Snapchat sync.
- `tiktok_native_insights.py`: one-campaign AI analysis, currently no browser tool.
- TikTok hierarchy currently does not preserve campaign pixel bindings or landing
  URLs. Advertiser-level pixel inventory alone cannot establish a campaign binding.

## Completed slice

`backend/tracking_diagnostics/evidence.py` defines bounded, strict records and
deterministic evidence comparison for all four platforms. It rejects mixed
owner/store/account/campaign/job evidence, stale and future observations, raw
payload fields, and records claiming media downloads or event writes. It never
equates multiple pixels with broken tracking, HTML configuration with event
delivery, a browser attempt with provider receipt, or provider events with sales.

22 focused tests pass. Explicit identifier namespaces prevent comparing TikTok
native numeric pixel IDs with browser pixel codes, or mixing platform IDs.
Google tag presence alone still does not prove a conversion action/label binding. This slice is not wired to a route or model, does not
collect live evidence, and is not eligible for deployment by itself.

## Required next vertical slice

1. Provision an isolated external browser execution service. The current tool
   inventory and repository expose no configured service that Mezan can call.
   ChatGPT's interactive browser is not a production worker endpoint. Do not
   install Chromium on the shared Mezan web server to work around this gap.
2. Authenticate backend-to-worker jobs, isolate every job in a fresh nonpersistent
   context, and enforce public-only egress at the network boundary, including
   redirects, subresources, DNS rebinding, and WebSockets. Application DNS checks
   alone are insufficient. Worker must run non-root with browser sandbox enabled.
3. Start with one public product page, one concurrent job, <=30 seconds, <=100
   observations, bounded script/document transfer, and no stored screenshots,
   HAR, cookies, recordings, HTML, images, or video. Block image/media/font requests
   before downloading. Worker returns only allowlisted pixel IDs, normalized
   event names, timing/status, and job binding; never raw URLs, bodies, customer
   identifiers, or tokens. No cart interaction or synthetic purchase.
4. Intercept analytics events before transmission to avoid injecting diagnostic
   traffic into campaign measurement. Label these as request attempts. This
   intentionally cannot prove receiver acceptance. Unknown transport formats
   remain unknown, never guessed. Do not accept tracking consent on behalf of a
   visitor; report unknown/denied consent as a limitation.
5. Add native provider collectors with exact owned campaign/ad-group bindings and
   explicit permissions/coverage/freshness. Reuse Snapchat. Obtain verified
   TikTok pixel list, binding and stats response contracts before coding endpoints.
   Meta shared pixels and Google's tag IDs/conversion labels require separate
   platform semantics; do not compare unrelated identifier types as if identical.
6. Add owner-only start/status routes, scope-derived trusted store URL, per-store
   bounded jobs, cancellation, idempotency, short TTL, and encrypted stored evidence.
   Refuse a missing worker/provider permission explicitly instead of returning
   successful-looking empty findings. No browser or polling loop on the web server.
7. Show progress, evidence timestamps, pixel-account comparison, and proven versus
   unverified findings in campaign analysis. Invoke diagnosis only for a tracking
   question or explicit owner request; cache by scope and time without stale reuse.
8. Add only sanitized diagnostics to model context. Do not run a live OpenAI test
   with campaign performance data until the specific payload and destination
   consent is resolved; the earlier automatic approval rejection still applies.
9. Run collector contract, isolation, resource, SSRF and UI cancellation tests,
   then an authorized real storefront check. No changes to existing event sources.
10. Follow the repository A/B/M release protocol only after the working vertical
    slice and deployment prerequisites are proven. No release intent in this WIP.

## Primary references reviewed

- https://playwright.dev/docs/docker
- https://playwright.dev/docs/api/class-browsertype
- https://developers.google.com/google-ads/api/docs/conversions/categories
- https://developers.google.com/google-ads/api/fields/v25/conversion_action

Playwright documents that root disables the Chromium sandbox, and that its Docker
image is intended for testing/development rather than untrusted browsing. The
worker deployment needs explicit isolation, not an unreviewed default container.

## Separate existing blockers preserved

TikTok publishing readiness still returns502 on verified PR1347. Portal login is
pending. The manually approved AI public reply succeeded previously; do not resend
it. Automated AI replies, Messaging, real product publication and real campaign
creation remain unverified/off as previously recorded. This task changes none of
those states.

## Documentation and deployment blocker checkpoint

The official legacy TikTok pixel-list documentation URL
`https://business-api.tiktok.com/portal/docs?id=1740858697598978`
redirected in the public browser to
`https://business-api.tiktok.com/portal/docs/about-the-guide/v2.0`.
The SDK lists GET pixel/list and pixel/event/stats, but this review has not
verified a complete response contract and campaign-binding coverage. Do not
invent response fields or ship a collector based solely on endpoint names.

No external browser service URL, credentials, or deployable isolated environment
is configured or available to this task. Production activation requires access
to such an environment, followed by worker/collector/routes/UI implementation
and a real storefront check. No diagnostic endpoint exists yet. Draft PR1348
is a durable WIP, not a finished or deployed feature.
