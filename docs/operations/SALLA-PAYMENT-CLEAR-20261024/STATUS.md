# Payment sync release — verified source, publishing pending

Task: SALLA-PAYMENT-CLEAR-20261024. Repository AMASI-SA/AMASI-SA. Implementation branch codex/salla-payment-clear-auto-20261024, PR #1473.

Source A fb10c3c98128de0ee1fbf83e9e9779ff8f0d0cc7; intent-only B 4df07544b87dfa9f72a902cc56f1e9532fb51008; previous reviewed base J 1fbb7ea4b54ab5574f3ee6937939e21440f88b0a. Source A passed all applicable 26 workflows. Real-Mongo CI logs explicitly executed settlement, older-event fence, durable retry/dedup and changed-owner tests; 35-test sync/recovery group passed. Local mapper/refresh pytest: 35 passed. Local real-Mongo attempt lacked running server and was stopped; not acceptance evidence.

Frozen intent downloaded from successful source workflow 40122507944, artifact 12572230866; source/base verified. B CI running; do not merge before completion. Guard in shared /app reported active=false before read-only fetch. Production changed: no. No publish or lease yet.

Rollback branch codex/payment-sync-rollback-baseline-20261024 retains J. Preserve unrelated files and newer production work.

Live Salla payment-event subscription remains unverified. Partner sign-in reached OTP. Automated secure OTP request was rejected by auto-review for field-description safety; no OTP entered, no workaround attempted. User handoff is needed to finish that verification after unaffected code/release work. Never record credentials or customer payloads.

Next: inspect B CI, merge without squash/rebase only if green and base unchanged; obtain generated v5 outputs from B CI; recheck shared lease; fast-forward /app preserving unrelated files; verify packages; prepare/prepublish; publish once; require explicit Deployment Succeeded and three verified identity probes. Refresh the already-paid order from authoritative Salla once and verify remaining=0 plus product eligibility. Complete partner subscription check before claiming automatic live delivery verified.
