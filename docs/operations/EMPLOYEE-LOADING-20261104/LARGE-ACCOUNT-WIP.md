# My Products large-account loading — WIP

User priority: restore usable loading on large employee accounts, then server-side ten-item stage paging. Existing Android65 stayed loading beyond47seconds on the selected large account; low-volume measurements are not representative. No chart retirement; those edits were discarded.

First slice adds authorized /supplier-dispatch-v1/overview using the same fresh current-order annotation and physical-piece stage summary. Skips image/registry hydration, supplier account/product/history materialization and supplier list. Does NOT yet avoid scanning active employee pieces or Order Engine DTO mapping. Legacy workspace retained for Android65. No production latency claim.

Local validation:37 supplier-dispatch/overview/index lifecycle tests pass. The mobile companion selects overview only on overview, loads details on stage entry, hides missing details rather than claiming no files, keeps fresh catalog read independent. TypeScript, on-demand and route-return harnesses pass. No new signed APK. No deployment lease, merge or publication for this slice.

Remaining: server-side ten-piece paging with fresh eligibility/count semantics and cross-page selection checks; heavy-account profiling; real Mongo HTTP coverage; governed release A/B and official mobile build recipe. Do not publish this WIP as completed performance repair. Preserve existing untracked frontend build directories and unrelated checkout changes.
