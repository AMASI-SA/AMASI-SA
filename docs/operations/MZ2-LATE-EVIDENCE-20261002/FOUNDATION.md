# Integration on reviewed Production83363097

This is a NEW task branch based on
`83363097d48e034dc7140a60c290efc684e1ffde`, with a normal two-parent integration
of `cc752823bb2c24f50ef250701921c4d729dc3638`. No original commit is rebased,
squashed or force-pushed. Original PR1237/48bb remains unchanged. All18 original
Integration-only commits remain reachable with their exact identities, together
with Production's13 commits. This avoids rewriting the reviewed history while
resolving the source combination on a separate branch.

Exactly two files conflicted:

- `backend/store_delivery_driver_app_routes.py`: retain PR1238 optional
  operational proof, explicit absent reference/URL and conditional proof binding;
  preserve C3 atomic cash capture and the gated existing native observer. Remove
  older duplicate proof keys in the same audit event that would otherwise
  overwrite explicit absence. No financial proof requirement is removed.
- `backend/tests/test_store_delivery_accounting.py`: retain Production's optional
  request-model assertions. Preserve the prior HTTP no-write test for C3's still
  mandatory cash confirmation; the old operational missing-photo422 expectation
  contradicts approved PR1238. Mandatory **financial** proof is covered by the
  actual optional-delivery/late-approval/native-recognition chain and existing
  Track F tests. No monetary, ownership, pause or no-write assertion is weakened.

Production's mobile evidence prefix, canonical COD amount projection, dispatch
and handover changes are preserved. The new late route already fits its existing
native allowlist; no auth expansion is needed.

The actual new producer tests exposed one composition defect: absent C3 reference
is `None`, whereas a retried request normalizes omission to an empty string.
Compare those two representations as the same absence, without changing stored
history. An actual differing reference still fails closed. Three cash amounts
(475/500/525 against COD500), actual native auth bridge, C3 immutability,
original-date recognition, paused423, one journal and same-delivery retry are
tested. The first run also corrected a new test's numeric representation
expectation (existing operational float500 versus sealed string500.00); the
500 amount requirement was unchanged.

Connected browser harness is prepared and NOT yet executed at this checkpoint.
Full Regression/Build follow this foundation. The inherited release intent is
historical and DOES NOT certify this new source. Fresh source/intent verification,
SSOT, Security/CodeQL and required acceptance remain gates. Existing Smoke B is
Acceptance-only; setup16/16 is not final Business UAT. Opening/Activation/physical
stock approval remain unexecuted. Release Readiness NO.

Production financial writes0; write-control UNCHANGED; no Production merge,
Deploy, Opening Post or Activation.
