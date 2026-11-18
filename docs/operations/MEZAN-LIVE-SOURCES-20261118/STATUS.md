# Live traffic source toggle and compact cards — 2026-11-18

PR1997, branch codex/live-sources-compact-20261118; original deployed release PR1994 merge d3632a33843e076dd7824933528dcc7d897bc32a. Prior compact checkpoint8ff4452842a4cfa74b9aba808d41243f7e2898b1.

Implemented: source card five rows/page, local pagination, compact spacing/header, footer removed. Per-minute hover/focus/click count tooltip. Shared Google/Mezan source switch for top pages and active visitors. Mezan aggregates reuse existing bounded/cache-protected30-minute read; distinct store/visitor counts5/30min and minute buckets; page_view counts by host/path with query/fragment stripped and sensitive routes grouped. No page_title exists; explicit path fallback. No extra polling, snippet change or external platform writes.

17 backend tests PASS. Frontend46 tests across three relevant suites PASS. Existing unrelated act warnings. git diff --check PASS. Visual size target70% not yet measured. Source switch does not mix providers or show Google values under Mezan; unavailable data uses dash, on fetch error Mezan aggregate cleared.

Owner explicitly authorized publication. No merge, new intent, lease or publish yet. Shared Emergent terminal disconnected; reload showed preview sleeping, Wake up servers clicked. Next recover terminal/read guard status and HEAD, finish visual acceptance, fresh governed sourceA build + intent-onlyB, clean clone rehearsal, CI, merge preservingA/B, FF/build/prepare/prepublish/singlepublish/verify3/finalinactive. Preserve unrelated work. Issue1006 full; use PR1997 comments ledger. Production changed by this followup: no.
