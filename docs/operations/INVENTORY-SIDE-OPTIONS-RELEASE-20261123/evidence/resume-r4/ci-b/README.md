# Final exact-B CI evidence — compact durable packet

Source A `deeefceffd9a1e37968513ac4f3833314bb415fd`; reviewed intent B `a92283678a25494c7d1870cd1675b8f30847aa6a`; B tree `868b8595dfeba299bf1f2dfaf1fc32b82d357033`; base J4 `28f6d4ca0fc2b7ea7f140432d8e32c768823b6a4`. This is a source/CI/rehearsal evidence packet, not a deployable checkout or a live Production verification receipt. The premerge PR snapshot inside `exact-b-ci/pr-final.json` and wording in the original FINAL-AUDIT report are historical.

## Results and source attribution

The independent B audit is PASS: 54 terminal check records (42 success, six correctly conditional skips, six superseded cancellations), with the latest 22 workflows comprising 21 successes and the expected TikTok branch exclusion. Tests overlap; do not sum suites.

| Evidence | Main tests or scenarios | Separate subtests |
|---|---:|---:|
| Operational backend | 6,921 | 151 |
| Durable demand read | 106 | — |
| Shipping summary/calculator | 16 | 18 |
| Operational frontend | 1,779 in 111 suites | — |
| Stock eligibility | 5,594 | Not re-counted in this packet |
| Supplier bundle Mongo | 120 | — |
| Browser journeys | Inventory 8 / simple balances 13 / financial 14 | — |

`BACKEND-RESULT-LINES.json` preserves the raw result lines and pytest command headers from exact-B job 138083430045, Operational run 45286310837. It independently confirms 6,921 main tests plus 151 subtests, 106 demand-read tests and 16 shipping tests plus 18 subtests, with the shipping step successful. It contains no source-A logs or environment sections. The workflow did not retain a separate shipping XML.

Readiness run 45286310883 passed Host Node 20.20.2 and 24.19.0 clean-clone adapter/runtime/package gates. All 387 generated payload records match between hosts; bytes and modes match, while tar mtimes differ. Package boundaries contain 1,821 frontend and 1,338 backend files. The original manifests, validator reports and selected build/proof/runtime metadata are retained.

## Restore and verify

The archive is split into 9 ordered parts of at most 512 KiB. Download every publication file into one folder, then run:

```bash
cat final-b-ci-evidence.zip.part[0-9][0-9][0-9] > final-b-ci-evidence.zip
sha256sum -c SHA256SUMS
python -m zipfile -t final-b-ci-evidence.zip
python -m zipfile -e final-b-ci-evidence.zip restored-final-b
```

Archive: **4,515,174 bytes**; SHA-256 **`1e7337403a20e2e7f7e80ed7c526622a05de34f9cd15fe7f2c8fb1a4c64b2729`**. `BUNDLE-INDEX.json` lists every payload file's exact path, bytes, SHA-256 and Git blob SHA-1. It does not self-hash. `INTEGRITY-MANIFEST.json` records publication files, part ordering and archive identity. `RAW-ARTIFACTS.json` binds the original ZIPs to their GitHub artifact metadata and exact B.

## Exact retention and omissions

All 15 retained raw ZIPs are unchanged: thirteen test/browser artifacts and two clean-clone adapter/package/runtime artifacts, totaling 3,731,947 bytes. The two redundant generated-outputs ZIPs (7,734,363 bytes total) are intentionally omitted; their artifact IDs, sizes, digests, retrieval metadata and original 387-file manifests remain in `OMITTED-ARTIFACTS.json` and the archive. Both hosts' original reproducibility proof, build-meta and backend runtime identity JSON are retained. JS/CSS/assets, dependencies, caches and arbitrary full job environment dumps are excluded. Canonical intent bytes remain in immutable public B and are bound by SHA-256 `f0b6f57069abe12ff6a8720f4eaded7552a47aeeedb0973021249545d726bea3`.

This compact packet supports independent checks of metadata identity, proof consistency and manifest bindings. It does not contain all generated asset bytes, so a reviewer cannot recompute every asset hash or rerun the full artifact-byte validator from this packet alone. That byte validation is preserved in the original audit and comparison receipts; repeating it requires the referenced generated-output artifacts or a new governed build.

## Publication data scope

These are CI fixtures and public source/test metadata, with no production customer records or private runtime environment dump. To preserve original ZIP bytes, the packet intentionally retains the already-public reviewed build contract frozen in B. The `REACT_APP_BACKEND_URL` origin is represented by its presence and SHA-256 fingerprint, not plaintext; the literal `NODE_ENV=production` and explicitly reviewed absent/empty defaults remain. Each retained environment object was compared with that exact frozen intent. CI toolchain versions, temporary paths and declared build commands are also retained. This exception does not permit credentials or arbitrary private environment values. `SENSITIVITY-REVIEW.json` records both reviewers' checks and their limits.

One raw XML testcase intentionally contains the public negative-test Mongo URI used to prove that credentialed loopback inputs are rejected. It exactly matches the literal fixture in immutable source A and is not an operational credential. That reviewed fixture and its source binding are documented separately in the sensitivity report; original raw ZIP bytes remain unchanged.

This packet does not claim a live publish, three-probe production verification, closed deployment lease or read-only live UI acceptance. Those receipts belong to the separate publication checkpoint.
