# Dashboard four-platform spend V1

The Mezan 2 Dashboard advertising card reads provider-reported spend for:

- Snapchat Ads
- Meta Ads
- TikTok Ads
- Google Ads

## Date behavior

- A single selected Riyadh date uses original hourly provider facts.
- A multi-day selection uses provider daily facts.
- Daily totals are never spread across hours as an estimate.
- While TikTok uses the temporary Make.com transport, its exact daily total is
  shown as a single labelled marker at the latest Make update hour. It is not
  presented as native hourly spend and disappears once native hourly facts exist.
- A connected provider without facts is displayed as awaiting data rather than zero.

## Sources

- Snapchat: selected account daily/hourly V2 facts.
- Meta: native account daily reporting and advertiser-timezone hourly breakdown.
- TikTok: native daily reporting and `stat_time_hour` hourly breakdown.
- Google Ads: GAQL daily/hourly reporting using `segments.date`, `segments.hour`, and `metrics.cost_micros`.

## Safety

Provider reads persist only isolated analytical facts. The feature does not mutate campaigns, ads, budgets, Salla, accounting, or Qoyod. Reporting rows are marked `accounting_eligible=False`.
