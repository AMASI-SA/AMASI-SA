# PR2080: disabled R3 rehearsal handoff

This is a draft helper and a launcher review, not an executable release instruction. No launcher or host command was run for this review.

## Fixed source and missing pins

- Base N: `02cd99e5b2b68c95341c87590abcf572c885e697`.
- Source A: `dd3a9ea29fce28e2fa87332cff4a6d46f776e0d3`.
- A tree: `ee385c9c71a19833e08b5e5d7470d94fe4b646f4`.
- Intent-only B: `9a500a21013a08f750009be656362481986eb208`.
- B tree: `2cbd2842ed2506642a1321a47e35f69a766fcf89`.
- Runtime release ID: `rg5-b2aa207d2f63107c64d9c0b66ce5cc567aa5ebb074753cf889227de4c448e008`.
- Intent SHA256: `0e0c7a6167dae598450358db6a1399b080cb7c69e176d28c5c31a0585ed287dc`.
- Candidate ref: `refs/heads/codex/operational-balance-server-r3-20261122`.
- `ENABLED=False`; the predecessor completion receipt pin remains `None`. Exact-B CI acceptance and the predecessor's actual released handoff remain prerequisites; fixing identity values does not enable the helper.

The first operation in `main()` validates those pins. The delivered draft stops before evidence reads, `/app` access, guard calls, networking, temporary directory creation, or a build. There are no CLI or environment overrides.

The previous-release pin must reference the actual completed PR2076 release and released-handoff receipt for N. Review its real schema before fixing `claim_paths`. Do not synthesize a receipt or infer completion from a merged PR, green CI, or an inactive guard. There is no separate serial approval pin, next-PR approval, or candidate-source approval record.

## Reviewed launcher mechanism

Use the isolated-rehearsal launcher pattern, not the stage/prepare launcher:

`docs/operations/CUSTOMER-INVENTORY-UNIFIED-20261122/RUN-HOST-REHEARSAL-2074.sh` at evidence commit `3be5f8df326ed27bec6976a344283e26a90510fd`.

1. Until all pins are frozen, the launcher itself must stop before any host action. A disabled helper alone does not prevent a launcher from fetching or inspecting `/app` first.
2. After review, publish the helper on the evidence branch. Pin its full evidence commit E, repository path and file SHA256 in the launcher. Keep E immutable; a branch name is not a replacement. Save the launcher in a later commit to avoid a self-referential commit pin.
3. Bind the predecessor receipt separately to its actual evidence commit, repository path and SHA256. The helper's `local_filename` is a basename, not a fixed temporary directory. The launcher writes the verified receipt beside the verified helper in its unique `/tmp` directory.
4. Before any Git fetch, verify the original `/app` path and original guard bytes/dependencies, call `python -I -B /app/scripts/production_release_guard.py status` with a ten-second external timeout, and require exactly `{"active": false}`. Capture and sanitize the result. The original advisory operation lock may be created; no lease is created. Do not hold that lock while calling `status`.
5. Require the shared production branch, HEAD=N and clean tracked source, then freshly read remote Production=N and the candidate ref=B. Preserve all unrelated untracked and ignored files; the historical untracked count is not a cleanup target.
6. Create a unique directory under `/tmp`, report its path immediately, clone `/app` with `--local --no-hardlinks --no-checkout`, and fetch exact evidence commit(s) only into that isolated clone. Use `--no-tags --no-write-fetch-head --no-auto-maintenance`. Keep the original remote URL in memory and do not print it or raw Git errors.
7. Read each file using `git show <full-commit>:<path>`, verify SHA256 before execution, and retain the checked bytes in the temporary directory. Execute the actual helper file using `python -I -B <absolute-helper-path>` so the adjacent pinned receipt resolves correctly. Do not pipe a mutable URL into Python or execute before checksum validation.
8. The helper independently repeats original-guard and N/B-ref checks, verifies A/B ancestry and trees, calls the original adapter's intent validators, then runs the original configured `yarn build` inside the isolated candidate clone. Its source/identity checks are explicit and do not depend on Python assertions being enabled.
9. Retain the helper, receipt, candidate clone, adapter log and result JSON on failure or success. The helper does not stage shared files, merge, prepare a lease, or publish. Passing a rehearsal is only rehearsal evidence; later release stages need their own fresh fences.

## Verification performed here

The source A parent and tree were read from local Git and match the pins above. The original guard and its two direct dependencies have identical SHA256 values at N and A. Verification of this draft is limited to syntax, static disabled-state checks, and independent source review. No host preflight, rehearsal, adapter build, lease, merge, publication or GitHub write was performed.
