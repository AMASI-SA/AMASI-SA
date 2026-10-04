# Optimized full-summary direct profile (1,000 orders)

Local isolated Mongo 127.0.0.1:27261, one actual V2 request, 1,000 products,
1 KiB item padding. Fixture database deleted in finally. Application source
was not edited. This sample preceded the proposed native-JSON codec change.

Endpoint/JSON wall time: 5.710 seconds. Mongo command durations sum: 0.108
seconds; response JSON: 0.010 seconds. Financial signature:
48f746e1b69e9212f30407079553c70bd2bc2b469fc5cdaabd72f79284dcdfad.

| Runtime boundary | Calls | Inclusive seconds |
|---|---:|---:|
| Internal decode | 27,018 | 1.656 |
| Store payload (includes encode) | 15,733 | 1.521 |
| Internal encode | 49,381 | 1.252 |
| Mapping INSERT | 13,914 | 0.376 |
| Mapping SELECT | 8,186 | 0.126 |
| Accumulator collect | 1,092 | 0.117 |
| Accumulator batched fee replay | 9 | 0.108 |
| Canonical match_settings via accumulator | 11 | 0.022 |

Boundaries nest; do not sum encode with store payload, or calculator calls
with accumulator phases. Direct wrappers use perf_counter around synchronous
calls; they add instrumentation overhead. This is diagnostic evidence rather
than a production latency claim.

The measured priority is internal codec overhead and repeated replay decoding.
Explicit transactions sharply reduced SQLite execution cost, while fee batches
reduced canonical calculation calls from the prior profile's 730 to 11. Further
financial-calculator changes have little justification from this sample.

A native JSON fast path with a lossless typed fallback is supported by this
profile. Preserve tuple/set/date/BSON/non-string dictionary key types; a plain
json.dumps success test alone is insufficient because tuples and integer keys
are silently changed by ordinary JSON round trips. Re-measure after this change.

Evidence: PROFILE-OPT-1000-DIRECT.json. Reproduction wrapper:
PROFILE-OPT-run.py direct. No new cProfile run began while the parent changed
the codec, to avoid mixing source versions.
