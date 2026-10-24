# Payment settlement — WIP

Branch codex/salla-payment-clear-auto-20261024; PR #1473; base 1fbb7ea4b54ab5574f3ee6937939e21440f88b0a.

Implemented explicit settlement normalization, fresh remaining-action replacement, payment event support, durable retry/deduplication with owner recheck, and payment-triggered eligibility refresh. Initial b57ba496e2a0a58b2df48b1b48dcac2695b4760f passed Fulfillment V2, G47, Security, operational balance/stock checks. Corrected misplaced webhook test method and added inbox retry/dedup/owner-change tests; latest CI pending. Local compileall passed; pytest unavailable locally.

Production changed: no. Actual webhook subscription remains unverified: Emergent local database lacked the target order; no subscription write attempted. Next: verify CI and live subscription, freeze protocol-v5 A/B intent, merge only after checks, inspect shared release lease, deploy once and verify identity plus live zero balance. Preserve other production changes. Rollback must use immediate prior verified release, never blind git reset.
