# R6.1 follow-up — bulk operation guard

Independent review after the307-pass R6 run found an incomplete protection:
ProtectedSpecialCollection blocked delete_one/delete_many, but allowed bulk_write
which could contain deletes or replacements. No current special package owner
uses bulk_write. This is a real gap in the broad history-deletion claim, not a
reason to reinterpret the original passing tests as coverage of it.

Added four synthetic Mongo cases for DeleteOne, DeleteMany, ReplaceOne and
UpdateOne inside an otherwise admitted transaction. All four failed against the
prior binding (DID NOT RAISE), then passed after refusing special-owned bulk_write
with special_bulk_write_requires_explicit_adapter. No existing test was weakened.
Ordinary native collection bulk behavior remains unchanged; only special-owned
collections require a future explicitly reviewed adapter. Real data untouched.

Source: one runtime file changed plus one new four-case test file. Exact predecessor
is materialized R6 fa54ae174f240ecb714b20bfb64bfc081de5f14b. hardening.json extends
rather than edits the historical R6 archive; current.py verifies48 cumulative paths.
The307 Backend result belongs to pre-hardening reconstructed R6. Current combined
and committed-tree results must be read separately, not assumed from it.

Local4 red then4 green; a combined local control run exceeded the execution tool
limit and is NOT claimed successful. Committed-source CI runs all controls and
native cases. No activation/merge/deploy/rollback. The higher-level native owner
invariants and the raw/legacy-writer limitations still apply.
