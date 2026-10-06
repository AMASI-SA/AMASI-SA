"""Pure diagnostic scheduling/statistics contract. No application imports."""
import math
import statistics
BASELINE = "bd8e1a4c377e61544b5cfe0f71f573c71abca89f"
CANDIDATE = "15f00ff0d072cf8e00b8d642a6390d2ef71c11ac"

def workloads():
    return [dict(name=f"{n}-A", count=n, tenants=("tenant-0",), concurrency=1, kind="single") for n in (10000,50000,100000)] + [
        dict(name=f"100k-same-A-c{c}",count=100000,tenants=("tenant-0",),concurrency=c,kind="same-key") for c in (3,4)] + [
        dict(name=f"100k-independent-{n}",count=100000,tenants=tuple(f"tenant-{i}" for i in range(n)),concurrency=n,kind="independent") for n in (2,3,4)]

def schedule(repetitions):
    if repetitions < 1: raise ValueError("positive repetitions required")
    for workload in workloads():
        for state in ("cold","warm"):
            for repetition in range(repetitions):
                # AB/BA alternation balances time-order drift; equal samples per SHA.
                sources=(BASELINE,CANDIDATE) if repetition % 2 == 0 else (CANDIDATE,BASELINE)
                for source in sources:
                    yield dict(workload=workload,state=state,repetition=repetition,source=source,
                               warmup_count=0 if state=="cold" else 1,profile=False)

def request_ids(workload):
    ids=workload["tenants"]
    return [ids[i % len(ids)] for i in range(workload["concurrency"])]

def percentile(values,p):
    values=sorted(values)
    return values[max(0,math.ceil(len(values)*p)-1)]

def summarize(samples):
    # One endpoint latency for A per independent repetition. Never pool tenants,
    # same-key requests, cold/warm or instrumented/uninstrumented observations.
    if not samples: return {"status":"INSUFFICIENT_SAMPLES","n":0}
    keys={(s["source"],s["workload"],s["state"],s["tenant"],s["profile"]) for s in samples}
    if len(keys)!=1 or any(s["profile"] for s in samples): raise ValueError("mixed sample populations")
    if len({s["repetition"] for s in samples})!=len(samples): raise ValueError("duplicate repetition")
    if any(not s["ok"] for s in samples): return {"status":"FAILURES_PRESENT","n":len(samples)}
    values=[s["endpoint_seconds"] for s in samples]
    if any(not math.isfinite(v) or v < 0 for v in values): raise ValueError("invalid latency")
    # Minimum expected tail observations, not a claim of statistical precision.
    # Report tail values only at >=20 expected observations beyond each quantile.
    result=dict(n=len(values),mean=statistics.mean(values),minimum=min(values),maximum=max(values),
                variance=statistics.variance(values) if len(values)>1 else None,
                percentile_method="nearest rank",precision="UNPROVEN: inspect order/drift and confidence intervals")
    for label,p,minimum in [("p50",.5,40),("p95",.95,400),("p99",.99,2000)]:
        result[label]=percentile(values,p) if len(values)>=minimum else None
        result[label+"_status"]="EMPIRICAL_ESTIMATE" if len(values)>=minimum else "INSUFFICIENT_SAMPLES"
    return result
