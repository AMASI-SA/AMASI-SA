# P1253: two separate single capacity samples

Baseline run: https://github.com/AMASI-SA/AMASI-SA/actions/runs/37522360728
Candidate run: https://github.com/AMASI-SA/AMASI-SA/actions/runs/37519368225

Exactly one measured A/Warm/100k sample per source, one unmeasured warm-up.
No profiling, cold, other workloads or repetitions. Original four-tenant
fixture and indexes retained. Same frozen harness hashes and package inventory.

Baseline HEAD bd8e1a4c377e61544b5cfe0f71f573c71abca89f
TREE 73cf78b1b170fc37458ccc39ec29e6caaab432e1.
Candidate HEAD 15f00ff0d072cf8e00b8d642a6390d2ef71c11ac
TREE 8f0c68640154b68606dbbb7c04e058a34dd0cd49.

| Metric | Baseline | Candidate |
|---|---:|---:|
| Endpoint seconds |31.732360|39.842267|
| Wall seconds |31.736715|39.847465|
| JSON seconds |0.004099|0.005013|
| RSS start MiB |107.3945|107.2578|
| RSS peak MiB |107.5469|107.4336|
| Mongo read commands |1464|1464|
| Mongo wire documents |182825|182825|
| Mongo driver duration sum seconds |1.194086|1.350157|
| Process CPU seconds |31.071064|39.275726|
| Event-loop thread CPU seconds |30.154440|38.146319|
| Spill peak bytes |181624832|181616640|
| Warm-up seconds |31.982203|39.820406|
| Reset seconds |3.906801|4.167923|
| Dataset preparation seconds |66.154103|96.851889|
| Fingerprint before seconds |16.666759|18.332727|
| Fingerprint after seconds |17.670755|19.335316|
| Signature seconds |0.001051|0.001081|
| Whole worker seconds |68.838164|84.005737|
| Whole controller seconds |176.992609|225.943736|
| Response bytes |54590|54590|

Candidate peak112652288bytes equals107.4336MiB or112.6523decimal MB;
the previous112.65MiB label was incorrect. RSS is window VmHWM,not Mongo RSS.
Driver duration sums must not be added to wall or CPU. Both use37find and
1427getMore; batch sizes/counts identical. Both spill stores1,cleanup complete.
Cache/coalescing counters unavailable,null. No Mongo failures.

Both financial signatures:
bd4ae5d387be731937920cd8a64c96d0a91a520266b9a95e3dbf63ce88fdd957.
Both full response signatures:
da05cd076c82018aad8c25f2ac10015f5f2928aff9a84ee500f63af4c6de3a06.

Raw dataset fingerprint baseline:
5fd24c6a90ac91dadb8c67a9eb79bd2294f6cfe58f6db0b8db23f1498370dacd.
Candidate:
cf1f719d1ae7eafc9a546f0877081822441539b45296c2717a9df10362a9f7fc.
Each fingerprint stable within its run; raw hashes differ across fresh seeds.
Raw hashing includes generated IDs/metadata. Counts/indexes match, but no
cross-run normalized payload comparison was recorded. Do not claim byte equality.

Both Ubuntu24.04.5,Python3.12.15,Mongo8.0.12,runner image20260927.320.1,
4logical CPUs,about16GB RAM,isolated replica set p1253test/27261,network none.
Mongo digest95a98776f273721a295b03098578b06bc10281bb56aa828c77e9f60ecc70b150.
Baseline CPU Intel Xeon Platinum8573C; Candidate AMD EPYC7763.
Separate allocations and rebuilt container image IDs. Observed endpoint
delta(Candidate/Baseline-1)=+25.56% does NOT establish code regression.
One sample each: no percentile or performance PASS/FAIL. RCA UNPROVEN.

## Planning limits

Observed A/Warm pair per repetition: measurement71.5842s,warm-up71.8026s,
reset8.0747s,fingerprints72.0056s,remaining worker overhead9.4571s.
Total232.9242s excluding seed,initial Mongo start and cleanup.
Observed pair seed163.0060s. Constant-cost arithmetic projection for this
A/Warm pair ONLY:40reps2.588h,100reps6.470h,200reps12.940h,
400reps25.880h,2000reps129.402h,plus preparation.

Full contract remains8workloads x2states x2sources xN:32N measured windows,
16N warm-ups. AtN2000:64000windows+32000warm-ups. Its actual total cannot
be inferred from A/Warm alone. Cold,multi-tenant,same-key and smaller workloads
were not capacity-measured here. No linear tenant scaling assumption and no
replacement of Cold by Warm. No precise revised full-matrix duration claimed.

17 diagnostic contract tests passed. Diagnostic execution SHA
52923e6fcb1346a6d5bd39014a005a1c30d37eb7; application/dependencies unchanged.
Raw artifacts retained in both GitHub runs and D:/codex-evidence.
No Merge,Prepare,Prepublish,Deploy or Production access/write. Stop for review.
