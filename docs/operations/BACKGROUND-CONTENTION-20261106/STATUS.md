# Background contention investigation

The user stopped dashboard work and redirected the performance repair to background activity: employee slowness occurs with all dashboard browsers closed. PR1784 is closed unmerged; its WIP remains recoverable on its own branch. Do not resume it as part of this task.

The owner supplied the production advertising scheduler status: enabled, running without a browser, 300-second interval. A completed cycle occupied 1177.17 seconds of wall time and a new cycle began approximately 12 seconds later. Snapchat finished partial with three of four expected requests complete. These are cycle/queue/network durations, not CPU measurements or proof of a sole cause.

The current source runs the native scheduler inside FastAPI. Its Mongo acquisition records the next due time relative to cycle start, and completion does not extend that time. When a cycle exceeds its interval, the next tick can reacquire immediately. This reproduces the near-continuous duty pattern in the supplied status; actual impact on employee requests still requires a controlled comparison.

Historical platform support evidence identified heavy Snapchat work coinciding with health-check failures and memory pressure. It predates the latest release and must be compared with current execution paths. The native v6 account reporting path and optional shadow reporting path are different owners. Several legacy loops were already disabled, and some AI launchers already use child processes; do not restart or duplicate them.

The first focused fix atomically records completion and the next due time using Mongo server time. Completed, partial and failed cycles now leave the configured interval before another replica may start. Three real-Mongo regressions failed before and pass after; the affected lease, scheduler, recovery, TikTok and governor checks pass 87 tests. Owner fencing and the saved result are preserved. CI also runs the new cases on its disposable Mongo service.

The fix remains on a WIP branch. No production configuration, lease, /app workspace or provider/financial state has changed in this task. Preserve Qoyod and operational processing completely. Next profile synthetic event-loop contention and verify exact data contracts and employee/health responsiveness. Cadence correctness does not establish the live improvement or resolve CPU/memory pressure inside an active cycle. STATUS.json records the reviewed base and sanitized production observation.
