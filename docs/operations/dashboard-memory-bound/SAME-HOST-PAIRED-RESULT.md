# P1253 same-host paired result

Run https://github.com/AMASI-SA/AMASI-SA/actions/runs/37524615896 completed
successfully. Execution diagnostic HEAD4e8cfef7fcf13e8bbbd11031fd9c5324af68a39f.
18 contract tests passed. Exactly20 measured samples:10 per source, A/Warm/100k,
concurrency1,one warm-up per sample outside measurement,no profiling. Original
four-tenant fixture seeded once; same Mongo container/data/indexes throughout.
Reset before every sample, then a fresh worker and identical warm-up. No other
workloads or full matrix executed. Total controller1227.969s including seed39.966s.

Baseline bd8e1a4c377e61544b5cfe0f71f573c71abca89f
TREE73cf78b1b170fc37458ccc39ec29e6caaab432e1.
Candidate15f00ff0d072cf8e00b8d642a6390d2ef71c11ac
TREE8f0c68640154b68606dbbb7c04e058a34dd0cd49.
Both sources clean and immutable. Measurement worker,application,dependencies,
dataset generator and thresholds unchanged.

## Shared environment

Runner GitHub Actions1000048071,job paired-capacity,4logical CPUs,
AMD EPYC9V45 96-Core Processor,about16GB RAM. Ubuntu24.04,
kernel6.17.0-1022-azure,runner image20260927.320.1,Python3.12.15.
Mongo8.0.12 real isolated replica set p1253test/27261,network none.
Mongo digest95a98776f273721a295b03098578b06bc10281bb56aa828c77e9f60ecc70b150.
Shared worker image837d9cfed779cde3c804efa1203cf2ba83e7056936c5176012feccb642207559.
Same default Docker resource limits. No separate allocation per source.

## Each pair

Delta=(Candidate/Baseline-1)*100. B=Baseline,C=Candidate.

| Pair | Order | B endpoint s | C endpoint s | Delta | B peak MiB | C peak MiB |
|---:|---|---:|---:|---:|---:|---:|
|1|BC|20.520|20.374|-0.710%|108.113|108.012|
|2|CB|20.413|20.469|+0.272%|108.359|107.582|
|3|BC|20.300|20.698|+1.964%|108.750|108.074|
|4|CB|20.270|20.374|+0.509%|108.961|107.750|
|5|BC|20.473|20.451|-0.108%|108.500|108.434|
|6|CB|21.019|20.589|-2.046%|107.324|108.648|
|7|BC|21.418|20.967|-2.109%|108.652|108.625|
|8|CB|20.425|20.609|+0.900%|107.652|107.660|
|9|BC|22.504|23.297|+3.521%|107.609|108.047|
|10|CB|20.032|20.356|+1.615%|106.469|107.957|

## Descriptive summary: median [minimum,maximum]

| Metric | Baseline | Candidate |
|---|---|---|
|Endpoint seconds|20.448897 [20.032180,22.504457]|20.528650 [20.355760,23.296926]|
|Wall seconds|20.452060 [20.035366,22.507980]|20.531930 [20.358952,23.300808]|
|RSS start MiB|108.097656 [106.308594,108.839844]|107.884766 [107.441406,108.578125]|
|RSS peak MiB|108.236328 [106.468750,108.960938]|108.029297 [107.582031,108.648438]|
|Process CPU seconds|20.050660 [19.651499,22.141089]|20.128918 [19.893911,22.827768]|
|Event-loop thread CPU seconds|19.522297 [19.126187,21.580952]|19.605300 [19.356659,22.156806]|
|Mongo driver seconds|0.778011 [0.757968,0.827864]|0.777524 [0.761964,0.888831]|
|JSON milliseconds|3.053851 [2.894522,3.274099]|2.999988 [2.893902,3.551253]|

Median endpoint ratio delta+0.390013%;median paired delta+0.390505%.
BC pair delta median-0.107675%;CB+0.509280%. Pair range-2.109468%..+3.521388%.
Endpoint standard deviation0.737288s baseline,0.890866s candidate.
Descriptive medians only; no threshold change or reliable p95/p99 claim.

## Invariants and evidence

All20 samples:1464Mongo read commands=37find+1427getMore;182825wire documents.
Batch histogram identical:7x1,1427x128,2x29,27x0,1x104. No Mongo failures.
Driver duration sums are not exclusive stage times; do not add them to wall/CPU.
Response54590bytes in every sample. Financial signature in every sample:
bd4ae5d387be731937920cd8a64c96d0a91a520266b9a95e3dbf63ce88fdd957.
Full response signature in every sample:
da05cd076c82018aad8c25f2ac10015f5f2928aff9a84ee500f63af4c6de3a06.
Dataset/index fingerprint unchanged after every sample:
0148d95fb00bb55a9bf6dbf08d853c5b551c0f0d112a780cf689660c291bd8a4.
RSS is window VmHWM for Python process,not lifetime or Mongo memory.
Spill store1 per run,all cleanup complete;median disk peak181624832B vs181620736B.
Cache/coalescing counters unavailable,null. Full per-run CPU/RSS/Mongo/JSON/
signatures and source/runtime identities retained in GitHub artifact.
Local evidence D:/codex-evidence/p1253-paired-37524615896 includes artifact.zip,
capacity-report.json,analysis.json,pairs.csv and all-runs.csv.

## Interpretation and stop

Previous cross-host+25.56% latency difference did not recur in this same-host
A/Warm experiment. Observed deltas change sign and are much smaller. Memory
and Mongo behavior remain close/equal as recorded above. This does not prove
the historical difference was exclusively caused by CPU hardware, nor exclude
regressions in unmeasured workloads/tails. RCA confidence UNPROVEN. No final
P1253 performance PASS/FAIL. Full contract remains deferred beyond this scope.
No application optimization,Merge,Prepare,Prepublish,Deploy or Production access.
Production writes0. Next action:user review only,no further run authorized.
