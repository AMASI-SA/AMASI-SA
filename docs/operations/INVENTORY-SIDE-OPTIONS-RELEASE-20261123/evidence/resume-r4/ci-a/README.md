# Exact source A4 CI evidence — review checkpoint

This folder preserves the completed Operational Balance Web evidence for source `deeefceffd9a1e37968513ac4f3833314bb415fd` (tree `a7317c4aa62c3d2266937bf60d4f0b2c4f62a27d`), run [45284191276](https://github.com/AMASI-SA/AMASI-SA/actions/runs/45284191276), attempt 1. All four jobs passed. It is evidence only: this branch is not a deployable source and does not authorize merging or deploying PR2097.

## Verified results

| Area | Main tests or scenarios | Separate subtests |
|---|---:|---:|
| Operational backend | 6,921 | 151 |
| Durable demand read | 106 | — |
| Shipping summary/calculator | 16 | 18 |
| Frontend | 1,779 in 111 suites | — |
| Worker | 4 XML testcase records | — |
| Inventory browser | 8 | — |
| Simple balances browser | 13 | — |
| Financial position browser | 14 | — |

Subtests are not added to the main pytest counts. The successful shipping step and its exact `16 passed, 18 subtests passed` output are retained in the backend log excerpt; the workflow did not retain a separate shipping XML. Browser scenarios use synthetic fixtures. Role screenshots were visually reviewed. The controlled benchmark is loopback Mongo/ASGI timing, not Production or browser/network latency.

## Restore and verify the raw evidence

The bundle is split into 7 ordered parts, each at most 512 KiB, to preserve the original bytes through the GitHub API. From this folder after downloading every file:

```bash
cat a4-operational-evidence.zip.part[0-9][0-9][0-9] > a4-operational-evidence.zip
sha256sum -c SHA256SUMS
python -m zipfile -t a4-operational-evidence.zip
python -m zipfile -e a4-operational-evidence.zip restored-a4-evidence
```

Reconstructed archive: **3,503,962 bytes**, SHA-256 **`deac1690275860a238b34997e89fe70465dbca470109907f1a6779e51e901afc`**. The original six GitHub artifact ZIPs remain unmodified inside `raw/artifacts/`. Their individual hashes match the GitHub artifact metadata. `BUNDLE-INDEX.json` lists every payload file's size, SHA-256 and Git blob SHA-1; it does not self-hash. `INTEGRITY-MANIFEST.json` binds the split parts and publication files to this exact source and archive.

The archive also retains raw final run/job/artifact metadata, source manifests, backend result lines, shipping step evidence and the independent candidate validation report. Paths in `SOURCE-BINDINGS.json` are relative to the restored archive root. Read `A4-OPERATIONAL-REVIEW.md` for scope and limitations.

## Scope at this checkpoint

This packet proves the source A4 CI results recorded above. Separate exact-B CI, Node20/24 adapter rehearsals, merge checks and deployment verification remain separate release gates. The packet contains no caches, dependencies, generated release builds, signed download URLs or credentials. Public GitHub provenance links are retained.
