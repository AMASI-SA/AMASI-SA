# Qoyod worker CPU R13 — WIP

Repository AMASI-SA/AMASI-SA; branch codex/qoyod-worker-cpu-r13-20261110.
Base36980489fb8bd6e7ee95f082f203320bd59eca2c (R12 #1867).
R12 published Nov10 21:58 Riyadh, official verify EXIT0/verified=true,
release guard inactive. No invoices sent by this task.

Production evidence: cycle heartbeat failed ownership match (RuntimeError at
19:04:56.633Z), canceled work and resumed the same candidates. Counters remain
129 batches,184 attempts,529 reviewed of773,0 sent. Read-only full scan failed
19:05:19.828Z after229921.94ms: unconfirmed_404,90 pages,8950 unique IDs.
The404 response must not be treated as absence without trustworthy evidence.

Found synchronous operational balance calculation on the shared worker loop.
Synthetic benchmark (no production data):10000 orders first22.195s and repeat
52.473s. Profile of3000 repeated orders made27,012,000 startswith calls, mainly
scanning every supplier obligation for every order. This establishes a concrete
blocking path; exact attribution of the production stall remains unprofiled.

R13 indexes supplier obligations and receipt facts within each pure calculation,
preserves colon-ID fallback and rollback behavior, and runs the pure reducer
via asyncio.to_thread. Authorization/CAS writes stay on the original loop.
No cadence, invoice gate, lease duration, provider404 rule or header change.
Optimized10000-order benchmark:first2.627s,repeat3.675s, unchanged result.

Regression-first: original2 tests failed (120600 prefix checks and heartbeat
blocked). Focused reducer/responsiveness25 passed; additional prefix/colon and
rollback tests pass (4 responsiveness tests total). Expanded operational suite
blocked at collection by missing local requests package; dependency setup and
full relevant tests remain. No R13 merge, release intent, lease or deployment.

Next: complete relevant tests/review; preserve WIP remote branch/DraftPR; freeze
v5 A/B, exact-head CI and isolated rehearsal before authorized merge/deploy;
verify actual cycle progress. Provider404 completion remains a separate blocker.
No shared /app modification while another release lease is active. Issue1006
is comment-limited; this status and PR body retain continuation evidence.
