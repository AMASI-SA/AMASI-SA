# Independent C2 Native review-history verification

Date: 2026-10-01. Scope: the uncommitted C2 history reader, route and index
changes in `C:/Users/amasi/mz2-c-rich-shipping-20261001`. No product source or
existing tests were edited by this verification task. Root owns implementation
and final source freeze; `ab_closure_audit` owns the repository regression suite.

## Finding reproduced and corrected

The first `coverage()` implementation inspected only current approved/rejected
operational reviews, using only their current revision keys. Real Native
rejection of revision 1 followed by actual driver resubmission to revision 2
therefore concealed a missing committed revision-1 event: the endpoint returned
200 with `unlinked_current_decisions=0`, both while revision 2 was pending and
after its genuine approval. Deletion was a deliberate fixture integrity fault;
no synthetic financial event or mocked writer was used.

Evidence:

- `test_c2_history_gap_probe.py`: external reproducible probe.
- `c2-history-gap-probe.xml`: **2 FAIL**, 3.35s, before the correction.
- `c2-history-gap-after.xml`: **2 PASS**, 3.60s, after root's correction.
- `c2-history-gap-False.json` / `c2-history-gap-True.json`: latest actual response.

Root's correction enumerates bounded prior operational revisions only to reveal
missing Native coverage. It includes pending queues and returns exact
`missing_native_revisions`, without reconstructing prior financial decisions.
The old decision may be pre-V2; missing evidence is disclosed, not invented.
Both real probes now identify revision 1 as missing and flag the affected review.

## Independent positive checks

- `read_driver_payment_history()` invokes fresh Native view permission; the
  route resolves owner from a fresh actor rather than caller claims.
- All event, operational coverage and journal queries retain exact owner scope.
  Cursor binding includes the query's owner and filters, with a real anchor.
- `validate_event()` validates the Native event seal and deterministic
  owner/review/revision key, approved destination identity, amount and decision.
- `present()` requires matching verified immutable journal metadata for every
  presented approval, preserving the original destination snapshot. Rejected
  decisions cannot claim a financial journal, and root added a same-key journal
  contradiction check following the other independent verifier's finding.
- Reversal history remains the original approval with a separately verified
  reversal flag. `test_c2_history_reversal_probe.py` uses the real reversal core:
  intact reversal yields `journal_reversed=true`; deletion of its audit yields
  409 `accounting_v2_journal_integrity_failure`.
- `c2-history-reversal-probe.xml`: **1 PASS**, 2.14s.
- All probe reads compare complete database snapshots for zero mutation. The
  imported fixture monitors Legacy collection accesses and drops its own unique
  loopback database. No Legacy accesses occurred in the successful runs.
- History reading does not invoke write-control mutations, create an economic
  writer, consume bank receipts, approve POS, or infer physical cash.
- The owner/kind/date/id index matches the main deterministic keyset ordering;
  collection/index initialization remains outside the read endpoint.

No additional material backend defect was identified in this bounded review.
This is independent verification of C2 implementation, not Full Regression,
complete 16-stage Business UAT, Smoke B or release authorization. Root was still
updating UI coverage wording when this report was written.

Production financial writes = 0. No commit/push, Merge, Deploy, Opening Post or
Activation was performed by this verification task. Tests used only the
isolated Mongo replica set at loopback port 27130 with dotenv disabled.
