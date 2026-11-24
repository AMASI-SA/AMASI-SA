# Saved response encoding evidence

This measures local synthetic payloads through the existing JSON encoder. It is
not a Production browser loading-time measurement.

The saved operational workspace read handler previously called
`JSONResponse(content=jsonable_encoder(response))` synchronously in its async
HTTP task. Report headers may contain up to 12 MiB of BSON; ready statements can
also contain complete paged histories. This can block unrelated HTTP work even
though the financial calculation already finished in the demand worker.

## Measured comparison

Both fixtures contain the same synthetic delivery fields, strings, small
nested dictionaries and empty reason arrays. A separate coroutine attempts to
wake every millisecond while the response is prepared. The table reports its
longest observed interval. Every compared response has identical bytes and SHA.

| Rows | BSON bytes | Synchronous preparation, ms | Helper preparation, ms | Maximum loop interval before, ms | Maximum loop interval after, ms |
|---:|---:|---:|---:|---:|---:|
| 5,000 | 1,702,835 | 152.76 | 144.06 | 153.90 | 18.92 |
| 20,000 | 6,837,835 | 621.96 | 593.90 | 623.10 | 77.92 |

The main benefit is event-loop availability, not elimination of serialization
CPU. Python's JSON encoder can still briefly hold the GIL. These values do not
establish the cause of the observed 13–14-second Production page data waits.

## Behavior and boundaries

`operational_response_encoding.encode_saved_response` uses the unchanged
`jsonable_encoder` and `JSONResponse` under the existing joined
`prepare_in_thread` cancellation lifetime. An async gate admits one encoder per
event loop; a process fence also prevents parallel encoders if an embedding
runs multiple loops. Queued cancellations start no encoding. A running
cancellation joins its owned worker before releasing the gate. The worker
checks cancellation between encoding phases.

The ready response handler reauthorizes the current request after encoding,
yields once to the sole disconnect listener, and checks the preparation stop
event before returning. Small 202 preparing responses retain their existing
path. No source reader, reconciliation, projection creation, or financial write
is added by this helper.

Tests cover byte/header equality for Decimal, datetime, Arabic and nested
values; event-loop progress; queued and running cancellation; process-wide
serialization across four loops; permission revocation during encoding; and
page disconnection before private response delivery.
