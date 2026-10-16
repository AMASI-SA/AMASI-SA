# Snapchat cycle coverage — 2026-10-16

State: source candidate, not deployed. Base97aa64777a9bbd7b10bd6b0b8001aba26297b926.
Branch codex/snapchat-cycle-account-coverage-20261016; PR1403.

Full-range source eligibility can return no Snapchat entities while the single closed-day check passes. Wrappers previously discarded account diagnostics. Persist actual per-account period, gates, blockers and counts, and display them in dashboard/recommendations. No fabricated recommendations or gate weakening. Older snapshots explicitly unknown. Read-only shadow endpoint also exposes account diagnostics for immediate diagnosis without invoking OpenAI or writing advertisements.

Verification:84 backend tests and28 frontend tests passed; all exit0. Live three-day shadow had0 unified entities versus53 campaigns/170 groups/200 ads from legacy observer. Exact live blocking gate is still unproven. Do not label this issue resolved until actual period diagnostic and fresh cycle are checked.

Production unchanged by this PR. Previous release97aa remains deployed and verified. No financial/ad writes. Preserve bank/terminal and supplier fixes. Release requires v5 source A, generated reviewed intent-only B, CI, official build/prepare/prepublish and visible success plusverify3. Next: finish CI, freeze intent, deploy under existing user authorization, inspect3-day account diagnostics and correct underlying data/code blocker if evidenced. Never promote missing costs tozero.
