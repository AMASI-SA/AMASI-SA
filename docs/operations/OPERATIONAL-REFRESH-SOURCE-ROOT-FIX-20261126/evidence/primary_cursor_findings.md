# Primary cursor experiment and limits

## Scope and execution

This is one offline synthetic-wire experiment, not a production database
benchmark. It used the existing Python environment, Motor 3.7.1, PyMongo 4.18.3,
Python 3.12.14 and the installed C BSON decoder. No dependency installation,
runtime source edit, production request or financial write was performed.

The exact command completed with exit status 0:

```bash
env PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 \
  /workspace/scratch/39c6dec7c37f/profit-test-venv/bin/python -B \
  docs/operations/OPERATIONAL-REFRESH-SOURCE-ROOT-FIX-20261126/evidence/measure_primary_cursor.py \
  --output docs/operations/OPERATIONAL-REFRESH-SOURCE-ROOT-FIX-20261126/evidence/measure_primary_cursor_result.json
```

The responder ran in a separate loopback process. It prepared all BSON before
admitting a measured client, so response construction and server socket writes
were excluded from client CPU and event-loop measurements. Each measured run
used a fresh client process. The responder was terminated and joined after the
four runs. Runtime reader/source hashes matched before and after the experiment.

The fixture contained 19,200 nested order documents (118.38 MiB of encoded
responses), three other primary collections with 4,800 nested records each
(33.96 MiB together), and a live synthetic snapshot with 38,400 obligations
and 19,200 facts (21.31 MiB encoded). The competing-source counts, nested object
distribution and bytes are declared assumptions, not production measurements.
The responder sent a 101-row first batch and later responses below 16 MiB.
Those sizes are fixture choices, not observations of production batches.

Both schedules used the existing source query, projection, 20,001 overflow
sentinel and 15-second read budget. All completed row streams had identical
BSON digests, including complete raw/proof containers and row order. The
orders-first alternative existed only in this experiment; runtime admission
was unchanged.

## Observations

| Run | Admission | Orders read, ms | Entire primary wave, ms | Client CPU, ms | Peak client RSS, KiB |
| --- | --- | ---: | ---: | ---: | ---: |
| 1 | Existing four-way | 6,595.703 | 6,604.766 | 6,645.304 | 1,521,040 |
| 2 | Orders first | 3,324.771 | 4,143.794 | 4,139.048 | 1,501,628 |
| 3 | Orders first | 3,156.522 | 4,240.889 | 4,205.840 | 1,486,224 |
| 4 | Existing four-way | 3,352.208 | 3,368.001 | 3,363.773 | 1,525,252 |

Unpacking BSON consumed 3.05–5.99 seconds of client thread CPU across the
primary sources. Recorded garbage collections consumed 1.40–2.26 seconds of
overlapping wall time. The maximum observed event-loop delay ranged from
425 to 851 ms. GC, socket, decoder and refresh measurements overlap; they
cannot be added as exclusive stage totals. Socket measurements cover all
sources, while individual decoder records carry the collection name.

The orders-first runs reduced the simultaneously materialized batch overhead
by roughly 19–39 MiB relative to the observed concurrent peaks. They did not
remove the much larger retained object graph: all source rows remain required.
Nor did they consistently reduce total time or CPU. The final concurrent run
completed faster than either orders-first run.

## Decision supported by the experiment

The experiment supports large materialization/GC work as a real mechanism and
demonstrates that ordinary row iteration is not a network operation per row.
It does **not** establish the cause of the production timeout, prove a uniform
throughput improvement from serial admission, or verify the complete 85-second
refresh budget. Do not present the first concurrent run alone as a baseline
speedup, and do not publish a serial-admission change on this evidence alone.

The existing installed driver drains buffered rows synchronously and calls
its executor on batch refresh. The application yields every 256 retained
rows. Thus 18,432 in the failed production trace is an application checkpoint,
not proof of a Mongo batch size. Full OP_MSG responses are decoded before
Motor delivers the batch, so first-row latency does not measure all subsequent
network, decoder, allocation, GC or competing-source work.

The next source investigation concerns duplicated query/preparation work
between primary financial sources and the profit context. It must preserve
the latter's distinct date cohort, normalized fields, projection shapes,
native versions and conditional awaiting-payment proof before any reuse can
replace a read.
