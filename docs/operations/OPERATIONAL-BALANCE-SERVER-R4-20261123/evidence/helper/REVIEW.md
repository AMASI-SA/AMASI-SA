# Independent review: PR2082 isolated rehearsal helper

Verdict: the corrected helper passes this bounded source and signal review. No concrete remaining blocker was observed within the reviewed scope. The original signal finding is resolved for the owned build wait and cleanup window. This is not a host rehearsal or publication acceptance.

## Exact reviewed identity

- Helper: `/workspace/scratch/dfe063f5aca0/r4-release-evidence/HOST-REHEARSE-2082.py`
- SHA256 before and after the harness: `6f85c5c53418d5229a4bcb05fdb31516e9191331b53c0bff51a17dcefec7ea0f`
- ENABLED: false; no main execution.
- Current Production/source parent N: `44122bcc986bd1dff1532b9ad62c0268e899a179`
- Source A: `aec203382b387c3812509084e77c2922aa860877`; tree `f133f04fd5759c57f5cce195e35ae03ba53ef6da`
- Intent B: `0bd6a3fadd33d6354ebdc8dec814bcb80b0d7218`; tree `4fa54935f420e2a546c2237c0fb7b0ef9ef8367d`
- Runtime release: `rg5-12e0f132b3ee688815cd2ec401bd34c43706d193492edacc7be6c5ca6e6ade8a`
- Intent bytes SHA256: `59da5ee6a86f3bfa5a1b8446c6aacb8e180144bc15ccaca3e414e1ec23b151ab` (1502688 bytes)
- Candidate remote ref: `refs/heads/codex/operational-balance-server-r4-20261123`; read-only API result matches B.

The local Git objects establish A as the direct child of N, B as the direct child of A, and A..B changing only release/release-intent-v5.json. The source/base/runtime/branch pins match the tracked intent. All three original guard/dependency SHA256 values match at N, A and B; the complete mapping is in pins-review.json.

## Preserved original finding

The earlier helper, SHA256 `278cf1676626bea460c183d25685030c32362a39f70681fef8839d492726f43c`, did not install SIGTERM/SIGHUP handlers. Either signal could terminate the helper without its cleanup/finally, while the separate build session continued and the receipt remained started. The finding was classified medium. The corrected helper installs Stop handlers and bounds group cleanup, including descendants that outlive their leader.

## Controlled signal evidence

The harness imports the actual helper without calling main. It invokes the original interrupted and stop_owned_build functions, and compiles only the actual signal-installation, build-window, save_report, and report except/finally AST. The Popen call is redirected to owned Python fakes. No clone, network, original adapter, guard command, /app access, lease operation, source edit or publication is performed by the harness.

Each case includes a descendant ignoring TERM/HUP/INT. After the first signal, nine additional TERM/HUP/INT signals are sent to the worker while cleanup is active. The original cleanup must terminate its owned group; emergency cleanup is not accepted as a pass.

| Initial signal | Fake leader behavior | Cleanup duration | Result |
| --- | --- | --- | --- |
| SIGTERM | leader-exits-early | 0.7503 s | PASS |
| SIGTERM | leader-ignores-term | 10.0446 s | PASS |
| SIGHUP | leader-exits-early | 0.7546 s | PASS |
| SIGHUP | leader-ignores-term | 10.0516 s | PASS |

All four workers exited 2 with the original Stop reason and a retained failed_or_interrupted receipt containing built=false and finished_at. Logs were retained. No owned running descendant survived; descendants were reaped by the test subreaper, leaving every recorded PID absent. No harness emergency cleanup was needed. The helper bytes were unchanged. The total command exited 0 with four passes and zero failures.

## Source review boundaries

The current coordination note correctly distinguishes the earlier PR2081 merge/CI record from actual publication completion. It introduces no new predecessor approval requirement. The helper keeps fresh original inactive guard, shared branch/HEAD/cleanliness, remote N/B and original guard hash checks before and after the isolated adapter. It checks exact A/B ancestry, trees, intent bytes and loader contract; retains evidence; sets isolated temporary/cache paths; and does not stage shared source, prepare/close a lease, merge or publish. The fixed signal cleanup sends TERM then bounded KILL to only its owned process group, even if the leader exits first.

This review does not establish exact-B CI acceptance, current host disk/guard/ref state, successful production build or publication. ENABLED remains false pending root's exact-B acceptance. The actual helper will enforce fresh host checks if it is later enabled and run by the owner.

## Evidence

- signal_harness.py: reproducible bounded harness; no helper main call.
- signal-review.json and four case directories: exact extracted code, logs, receipts, process states and durations.
- pins-review.json: local object/intent/guard verification.
- remote-candidate-ref.json: candidate GitHub ref snapshot.
- evidence-sha256.json: SHA256 manifest excluding itself.

Review completed at 2026-11-23T12:01:39.492559+00:00.
