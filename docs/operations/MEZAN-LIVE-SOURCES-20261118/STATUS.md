# Live sources compact layout follow-up

2026-11-18. Previous PR1994 production release d3632a33843e076dd7824933528dcc7d897bc32a verified three times, inactive lease. Historical evidence: PR1994 comment6989860812 and handoff0806066f490f709983069389f35df44f1ab0d673.

Current branch codex/live-sources-compact-20261118: five source rows per page; local previous/next; remove footer explanations/timestamps; compact header/spacing. GA active minute bars expose exact minute/count via hover, focus and click. No additional network requests.

Validation: 44 tests passed across live sources/mobile/cardRefresh suites, then new tooltip test added and all9 mobile tests passed (45 total unique tests). git diff --check passed. Existing unrelated CampaignHoverBadge act warnings remain. Height reduction target about70%, not yet browser-measured. Production changed by this follow-up: no. No new release intent, lease or deployment.

User additionally asks for Mezan/Google source toggle shared by top pages and active users cards. Feasibility inspected: first-party events contain page_view, page_url, visitor_id and received_at, but no page_title. Needs a bounded aggregate for distinct visitors and minute buckets, page-view counts, page names from safe catalog mapping or sanitized path fallback. Must distinguish observed Mezan visitors from GA active users. Not implemented yet.

Next: review compact UI, implement source toggle if proceeding, then fresh v5 A/B build and CI before any deployment. Keep original frozen A/B unchanged. Issue1006 full; follow-up PR is continuation ledger.
