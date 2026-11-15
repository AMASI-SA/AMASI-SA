# Provider fee display repair — 16 November 2026 (Riyadh)

Production baseline is PR1950 merge `236a428364bc8163a684795ed1be87e237476f18`. Its previous publication was successful; the user reports a remaining product defect, not a release identity failure. The live provider table hides calculable estimated fees when any component is incomplete, and long inline explanations make provider rows excessively tall.

The owner explicitly requires all explanations below the table under «توضيحات». Keep numeric review controls usable. Preserve actual/estimated separation, true zero versus unknown, native-currency scope, existing bank/principal/refund controls, and demand-only reads. No broad source refresh or financial posting is part of this repair.

Root read current AGENTS, prior final task ledger and current remote production ref. Original /app is exact baseline, tracked-clean, official guard inactive,120 existing untracked entries; coordinator marker absent and not enabled. Root created only an isolated detached checkout at `/tmp/provider-fee-display-20261116`.

A synthetic exact-baseline reproduction using the real provider table builder exited1 for the intended assertion: the supplied known estimated subtotal remained hidden as null. The expected subtotal comes directly from the fee accumulator, never total minus actual. Receipt is adjacent.

Backend and frontend source changes are in progress. No integrated test pass, new merge or publication is claimed. The reviewed diagnostic helper reads one explicitly scoped saved state and observes the existing pure projection; it performs no refresh or write and outputs bounded anonymized predicates. Root read the entire helper, SHA256 `c310ac2ce9fc218a5b3c30387a3292ad2e94f19fdf8c70ae43ca0fc1a7bd812d`. It has not run against live data yet.

Next: scoped read-only predicate diagnostic; finish additive computed-estimate metadata and concise table/below-table explanations; preserve source checkpoint; run original isolated regression suites and independent review; then follow the existing separately authorized v5 merge/publication protocol. Issue1006 remains at its known comment limit; this task record and PR carry continuity.

Evidence branch only: do not merge or deploy.
