# Payment sync release — verified source, publishing pending

Task: SALLA-PAYMENT-CLEAR-20261024. Repository AMASI-SA/AMASI-SA. Implementation branch codex/salla-payment-clear-auto-20261024, PR #1473.

Source A fb10c3c98128de0ee1fbf83e9e9779ff8f0d0cc7; intent-only B 4df07544b87dfa9f72a902cc56f1e9532fb51008; previous reviewed base J 1fbb7ea4b54ab5574f3ee6937939e21440f88b0a. Source A passed all applicable 26 workflows. Real-Mongo CI logs explicitly executed settlement, older-event fence, durable retry/dedup and changed-owner tests; 35-test sync/recovery group passed. Local mapper/refresh pytest: 35 passed. Local real-Mongo attempt lacked running server and was stopped; not acceptance evidence.

Frozen intent downloaded from successful source workflow 40122507944, artifact 12572230866; source/base verified. B CI running; do not merge before completion. Guard in shared /app reported active=false before read-only fetch. Production changed: no. No publish or lease yet.

Rollback branch codex/payment-sync-rollback-baseline-20261024 retains J. Preserve unrelated files and newer production work.

Live Salla payment-event subscription remains unverified. Partner sign-in reached OTP. Automated secure OTP request was rejected by auto-review for field-description safety; no OTP entered, no workaround attempted. User handoff is needed to finish that verification after unaffected code/release work. Never record credentials or customer payloads.

Next: inspect B CI, merge without squash/rebase only if green and base unchanged; obtain generated v5 outputs from B CI; recheck shared lease; fast-forward /app preserving unrelated files; verify packages; prepare/prepublish; publish once; require explicit Deployment Succeeded and three verified identity probes. Refresh the already-paid order from authoritative Salla once and verify remaining=0 plus product eligibility. Complete partner subscription check before claiming automatic live delivery verified.

## Publication in progress

All applicable B checks passed (only unrelated TikTok workflow skipped). PR #1473 merged as 9df7ed03222bd7adcf3a8dff00c56a901a90e4ca; merge tree matches B. CI generated outputs artifact 12572267195 from run 40123307212 passed SHA-256, intent-byte and package checks locally and in /app. Shared /app fast-forwarded preserving unrelated files. Prior generated outputs backed up to /root/payment-sync-prior-release-20261024.

Guard prepared by codex-payment-sync-20261024 at 2026-10-24T19:08:58Z; prepublish ready=true. Runtime rg5-0128e1df393b5a29e97bc383022bafa433d89c67f73330f17b25259e2cad8697. Publish changes clicked exactly once; UI Publishing. Do not click publish again. Next: wait for a new explicit Deployment Succeeded newer than previous Oct 24 21:41 Riyadh; run guard verify against https://mezansalla.com, then status inactive. Live order verification and subscription OTP remain pending.

## Published and verified — remaining account handoff

Production changed: yes. Emergent displayed newer Deployment finished Oct 24 22:18 Riyadh, runtime deployment d0a12b3. Publish clicked once. Guard verify exited 0: verified=true, checks=3, source fb10c3c98128de0ee1fbf83e9e9779ff8f0d0cc7, deployment 9df7ed03222bd7adcf3a8dff00c56a901a90e4ca, runtime rg5-0128e1df393b5a29e97bc383022bafa433d89c67f73330f17b25259e2cad8697. Guard status active=false. Evidence /root/payment-sync-candidate-20261024/production-verify.json; previous generated outputs retained in /root/payment-sync-prior-release-20261024.

Live target order reloaded and refreshed once through the ordinary Salla refresh button. Success toast shown; former outstanding-debt display disappeared, payment card now says paid via Mada. UI does not render a numeric zero for fully paid orders, so do not call this a direct numerical DB observation. Source-addition button remains disabled with generic product-readiness notice; do not claim review eligibility fixed for that target. Local Emergent database has records but did not contain target under string/int/reference lookup; it is not evidence of live target financial fields.

Automatic processing code is deployed and covered by real-Mongo tests, but actual payment-event subscription has NOT been confirmed. Partner login reached four-field OTP; auto-review rejected automated OTP input for field-description risk. Do not retry via alternate credential paths. Next safe action: user completes partner OTP through manual browser handoff, then inspect existing app webhook subscriptions, preserve other events and signature security, enable order.payment.updated if missing, and verify delivery. Also diagnose the live target product-readiness block before claiming the added-product workflow complete. No product approved and no provider financial facts manually rewritten.
