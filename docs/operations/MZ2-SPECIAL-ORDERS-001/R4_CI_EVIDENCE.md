# R4 native checkpoint evidence — implementation continues, not release acceptance

Run 36425847676 completed successfully on transfer HEAD
`7fa9a7dca081e23e1b87e87c42e76149207387b1` AFTER applying the exact preserved
15-file R4 patch. The branch at that HEAD held a transfer, not the native source.
All 15 exported Git blob identities and local SHA256 source bytes match. This
checkpoint installs those blobs verbatim into normal source paths.

Artifact10971233846 (10084 bytes) downloaded and hashed independently:
`4f8e260c0bca804e37369d908ece16add19d853d5f211fe070b7f65ba4e6ef63`.

- Baseline JUnit:114 cases,113 pass,1 collection skip,0 failures/errors.
  XML SHA256 `71bbfdb621ee2f76a009b596863e3e0afa1146f2feae893ee7340f402da1ee23`.
- Candidate JUnit:256 cases,255 pass,1 collection skip,0 failures/errors.
  XML SHA256 `3ccd2af4a7590f084d28cd140a9b7a05493635099ada601052d508406c24a215`.
- Same skip on both: `tests.test_store_delivery_accounting` imports
  `mongomock_motor` with importorskip. The test was not weakened. Add the pinned
  dependency for the next committed-tree CI, rather than calling this skip a pass.
- Candidate includes142 special-order tests plus the same113 executed ordinary
  regressions. Repeated baseline passes are not additional distinct coverage.

Native tests use actual existing supplier invoice closure, inventory decrement,
handover/driver/receipt routes and MZ2 ledger writes against a dedicated synthetic
loopback Mongo replica set. They assert transaction rollback, replay, local source
isolation, no doubled supplier payable or Salla sale, exact cash/fee segregation,
required bank movement and immutable rejected/resubmitted receipt evidence.
Some fixtures start at a prepared/scanned boundary; these tests do not establish
the complete review-to-label-to-device lifecycle or real operator UAT.

No server.py activation, Merge, Preview/Production deployment, Android publish,
live merchant/provider/financial writes or release lease/intent changes occurred.
Continue partial/net settlements, reassignment, full preparation/assembly/label,
persistent Salla sidecar and report/client integration. Keep Draft PR1171 and
separate UI1167 preserved. Historical R3/R4 transfer bundles are provenance only
once materialized; never apply them again to the resulting source.
