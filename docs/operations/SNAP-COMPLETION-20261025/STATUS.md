# Snapchat completion — 2026-10-25

User authorized parallel implementation and a separate governed production release and live test after each completed increment. Root owns integration and production publication; three parent-owned agents independently review readiness, media/publishing, and autonomy without external writes.

Repository: AMASI-SA/AMASI-SA. Starting Production: 73901e0e5caa5d75ef9e97ccaa8a6336bc0a7af9. Task branch: codex/snap-completion-20261025. Existing Google and TikTok checkouts and changes remain untouched.

## Increment 1 — campaign amount evidence

Reapply only the source correction from unmerged PR1415 onto current Production. Native amount proof, complete TOTAL period, positive finite FX and complete calculator coverage must reach the unified campaign row. All existing evidence gates remain enforced. Rename the original regression to test_snapchat_reporting_v2_entity_amount_evidence.py so the existing Snapchat Reporting V2 Shadow workflow actually runs it.

Fresh baseline: targeted actual-reader regression failed in three healthy cases (USD, SAR and native zero); twelve incomplete/invalid cases passed. Command: google-test-venv/bin/python -m pytest -p snapchat_v2_test_bootstrap tests/test_snapchat_reporting_v2_entity_amount_evidence.py -q --tb=short. Result: 3 failed, 12 passed, 0.57s.

Source correction applied. Fresh affected checks passed: 80 tests, exit0, 2.14s (amount regression, entity performance, range proof, Phase5, unified cutover, account analysis and shadow). Two inherited Pydantic deprecation warnings. The independent readiness review confirmed the propagation fix and identified the existing CI filename mismatch; the renamed regression is selected by the unchanged reporting workflow wildcard.

Next: finish period/account regression, push source checkpoint and Draft PR. Then generate a fresh protocol-v5 source/intent pair from exact source CI, pass final release checks, serialize with the shared Production lease, publish and verify three probes. Live acceptance uses eligible candidate counts; zero recommendations alone is not a failure.

## Parallel follow-ups

- Saudi hierarchy readiness needs real per-level/per-day producing-run evidence; missing facts must never be declared zero. Provider-confirmed empty hierarchies and proven zero-commerce campaigns need separate reviewed treatment.
- Media upload must create media, upload multipart bytes, poll processing and bind the verified media to creative/ad in the selected account, with bounded bytes/time and uncertain-outcome reconciliation.
- Autonomy must add an explicit per-account policy and governed dispatch. Existing owner approval, freshness, accounting, drift, serialization, audit and rollback controls remain the baseline. No arbitrary spending limit is invented.

Production changed by this task: no. Advertising writes by this task: zero. No release intent frozen and no Production lease acquired yet.

Issue1006 reached its GitHub comment limit; the task PR and this committed status document will carry continuation evidence and link back to that canonical ledger.
