# Courier print latency

Base production f7b5a81cf92cfb43b1c7277ed95641f7f3c87d9f. No live Salla calls, financial writes or deployment.

Resolve the already ingested owner-scoped Salla internal ID as a routing hint and validate every fresh returned ID/reference. Fall back to keyword search only when no stored ID exists. Reuse resolved identity for final fresh details rather than repeat keyword search. Never reuse status or skip the final completed/carrier/local fence.

Integration with mobile courier queued print: saved read + final guarded regular read produces equivalent canonical print data (regular route adds three unused assignment metadata fields). KnownID combined providerGETs8 to5; missingID fallback8 to7. Synthetic80ms perproviderIO before769–844ms, after487–579ms. Root independent after494–554ms. This is isolated provider-double measurement, not a claim that the real reported minute is eliminated.

Tests:26 focused including real isolatedMongo;325 existing legacy/courier/current/saved regression checks PASS. Baseline has4 intended count failures with data-equivalence controls passing. Root independently reran26PASS7.12s. CI includes new latency suite with disposable replica set.

Remaining: provider state can change after finalGET; no atomicity claim with Salla. Actual device PDF/printer acceptance remains untested. This patch must be independently reviewed/deployed; mobile correction requires a new reviewed APK.
