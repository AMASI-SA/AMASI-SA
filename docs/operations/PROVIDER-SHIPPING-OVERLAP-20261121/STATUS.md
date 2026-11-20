# Provider shipping overlap correction

Owner authorized diagnosis, correction, publication and verification. Base production 541b3930844175c37e319b0dfc5d1ccb5ba2b626 includes PR2035 bank/provider and PR2036 shared positions. Task branch codex/provider-shipping-overlap-20261121.

Read-only production review reproduced historical payment holds caused by outgoing prepaid courier expenses sharing the order identity, despite no provider settlement. Correct overlap classification excludes only fully allocated, validated shipping payables. Explicit provider allocations, bank capture identities, refunds, unknown or incomplete movements continue to block. No financial data was changed.

Validation checkpoint: 100 focused pure/engine/history tests passed. One HTTP integration test could not run because localhost Mongo replica set port27305 is absent; full CI required. Shipping/currency/provider matrix and repeated reconciliation regression covered. Shared /app guard active:false observed before changes; /app unchanged.

WIP: run full CI, freeze intent-only B from governed A/B build, inspect latest production and guard, shared host rehearsal then authorized publication and three-probe verification. Production unchanged by this task. Never approve real receipts or change historical accounting entries. Stored live snapshot still predates release; do not claim resolved balances before fresh evidence. PR is continuation ledger because Issue1006 is at comment capacity.
