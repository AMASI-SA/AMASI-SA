# Release Guard Intent lineage repair (Draft PR1265)

Independent Release/CI only, based on a7977f4cfc1ef0a721fc25d783661332f32fe3b6. No PR1263/1264, operational product code, Production branch, Intent, release ID, artifact or lease changes. No Prepare/Prepublish/Deploy; Production writes=0.

## Trust and proof
The caller must authenticate the immutable Production base using the existing release identity protocol. Git proves provenance, not owner approval. No candidate policy file or branch name establishes trust. The validated anchor's first-parent Production spine supplies eligible checkpoints; candidate history is traversed across ALL parents without path simplification. Linear commits cannot change the Intent entry. A changing two-parent merge must copy its second parent's exact mode/type/blob/bytes, that parent must be on the trusted spine, and its checkpoint must advance the inherited provenance. Missing/shallow/replaced/grafted history and unsupported multi-parent merges fail closed. Final entry must match the approved base. Historical trusted Production releases remain the trust boundary, not newly created candidate transitions.

All three workflow gates and adapter/production guard share scripts/release_intent_history.py. Existing source advancement, prior P/J identity and candidate A/deployment B intent-only rules remain. New real-Git tests cover positive import chains and malicious histories. Old mock-SHA guard test isolates the unrelated deployment delta check; real-Git tests exercise the shared proof and guard integration.

## Status
Implementation checkpoint: first Linux Backend preflight PASS (173 tests) on 6f2dea71. Additional adversarial tests and Windows fixture portability fix now included; final-head validation pending. Draft opened with documentation before implementation. Exact checkpoint and results recorded in PR and Issue1006. Do not merge or deploy without separate approval.
