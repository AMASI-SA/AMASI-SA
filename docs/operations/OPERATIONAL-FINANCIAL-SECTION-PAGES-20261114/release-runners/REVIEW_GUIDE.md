# PR1921 original-workspace release runners

These files are execution templates for the authorized operator. Preparing or
reviewing them does not run a release. The original `/app` workspace and its
editor buffers were not accessed while creating these templates.

## Reviewed identities and fields still blocked

| Identity | Value |
| --- | --- |
| Production branch | `hotfix/prod-snap-meta-final` |
| Source A | `0dd26a620a0acc4235d6fdfa0a3a8e714982fcae` |
| Previous production J | `29ec1e07eb7d1a23647cadff5bac26149accfa99` |
| Intent-only B | `5fc08eef0b430b53911934d9f866ec7b13b61b47` |
| Reviewed B tree; required M tree | `d636970f1a954530cf7845e408e05362af160cfa` |
| Runtime release ID | `rg5-5a1f26faa5108bc63621c816cdbd90697e422290a4245a460ff6020e6c97dc30` |
| Intent byte SHA-256 | `ad0a524b2de8da1a734e46bfeb4d851f9743ae28293eedb361075c41fcc4a4b2` |
| Lease actor | `codex-financial-section-pages-1921` |

The operator must fill and review the following before execution:

1. `REVIEWED_M`, after checking the actual normal merge has parents J and B,
   in that order, and exactly B's reviewed tree. All three original-workspace
   runners must contain the same complete M SHA.
2. `EXPECTED_UNTRACKED_COUNT`, after a fresh read-only review of the original
   nonignored untracked inventory. It is deliberately `None`; the previously
   observed count of 120 has not been adopted as a fresh baseline.
3. Gate `EVIDENCE`, after this release's original-workspace build rehearsal has
   succeeded. It must be that run's private `/tmp/mezan-pr1921-original-*`
   directory. Verification and preservation receive the same directory as an
   argument. Never use the PR1919 directory or evidence from a stopped attempt.

Configuration validation rejects unfilled M/count before creating evidence or
touching `/app`. The gate also rejects its unfilled evidence path lexically,
before testing directory contents. Verification and preservation validate the
reviewed identities before accessing their supplied evidence directories.
Rehearsal creates a new private evidence directory; later stages refuse to
overwrite a stage's existing evidence. A failed or partial stage requires
inspection, not an automatic retry with a different label or edited result.

## Provenance and scope

The four prior runners were read from immutable handoff commit
`21d4a86f10c91239f6b2812dd258e6713916ab3e`, under
`docs/operations/OPERATIONAL-FINANCIAL-LOAD-RECOVERY-20261114/release-runners/`.
`TEMPLATE_MANIFEST.json` records both the prior file hashes and the new hashes.
The controlled differences are PR/actor/evidence names, A/J/B/tree/runtime/intent
pins, invalid M/count/evidence placeholders, and the gate's additional lexical
check for its evidence placeholder. The release-guard, process-exit, ancestry,
collision and preservation mechanisms are retained.

No runner publishes, edits source, force-updates Git, resets the worktree,
deletes original untracked files, automatically quarantines files, restores
files, clears another lease, or saves/discards an editor buffer. Rehearsal
`--build` permits only the reviewed fetch, fast-forward and official build
contract. The official build adapter manages its governed ignored generated
outputs as specified by `AGENTS.md`; these outputs are not part of the
nonignored-untracked preservation manifest.

The preservation manifest covers `git ls-files --others --exclude-standard`.
It records path, type, mode, size and SHA-256, rejects unsupported types or a
file changing during its read, and checks target-tree path collisions before
the fast-forward. It includes symlink targets without following those links,
and rejects symlink parents. It does not enumerate ignored generated files.
Ignored backend bytecode is a separate fail-closed rehearsal check. If found,
the runner stops; it does not infer ownership or remove/quarantine it.

The only permitted difference in nonignored files after the official build is
a byte-for-byte prefix-preserving append to `.emergent/stripped_env_names`,
with the same file type and mode. Every other path, byte hash, mode, size and
inventory entry must match the new `04-before.manifest.json`. The helper keeps
the original sidecar prefix privately for that comparison. Logs and manifests
stay in the operator-owned private evidence directory; do not paste file or
log contents into public records.

Filesystem checks cannot observe the unsaved `Untitled-1` buffer. The operator
must separately inspect and record that the same buffer remains open and
unsaved before and after the release, without saving, discarding or changing
it. A count of untracked files alone is not proof that their contents survived.

## Execution order after the missing identities are reviewed

These examples intentionally use unfilled paths and M. Root stages the
reviewed files outside `/app`, preserves their hashes, and substitutes the
actual reviewed values before use. Keep the preservation helper beside the
rehearsal runner that it imports.

```bash
reviewed_runner_dir='/tmp/<STAGED_REVIEWED_PR1921_RUNNERS>'
reviewed_evidence_dir='/tmp/<NEW_SUCCESSFUL_REHEARSAL_DIRECTORY>'
reviewed_merge_sha='<REVIEWED_NORMAL_MERGE_M>'
```

### 1. Original-workspace rehearsal, with no lease

Freshly inspect the official guard and original editor state. Run only if the
guard is inactive, no incomplete lease directory exists, the original branch
is the production branch, tracked files are clean, and HEAD is J or the exact
reviewed M. The runner repeats these checks before consequential stages.

```bash
python -B "$reviewed_runner_dir/pr1921_original_rehearsal.py"
python -B "$reviewed_runner_dir/pr1921_original_rehearsal.py" --build
```

Each invocation has its own evidence directory. The gate must use the
successful `--build` invocation's directory, not the preflight-only directory.
The build runner verifies B directly follows A, M has exactly J/B parents,
both B and M trees match the reviewed tree, A-to-M changes exactly the intent
file, and raw intent bytes match the reviewed SHA-256. It rechecks clean
tracked state, preserved nonignored files, absent ignored backend caches,
and inactive guard around the fast-forward/build. The build command has no
wrapper timeout and cannot create a lease through these runners.

### 2. Prepare, then immediately prepublish when ready to publish

```bash
python -B "$reviewed_runner_dir/pr1921_original_gate.py" --stage prepare
python -B "$reviewed_runner_dir/pr1921_preserve_untracked.py" \
  --evidence-dir "$reviewed_evidence_dir" --label after-prepare

python -B "$reviewed_runner_dir/pr1921_original_gate.py" --stage prepublish
python -B "$reviewed_runner_dir/pr1921_preserve_untracked.py" \
  --evidence-dir "$reviewed_evidence_dir" --label after-prepublish
```

The gate verifies the successful rehearsal's identities, actual build exit,
intent byte hash, ancestry evidence and preservation. Prepare requires a fresh
exactly inactive guard; prepublish requires the full active lease to equal
this actor's saved prepare result, including its recorded identity. Each
stage checks the guard again afterward. Only the official guard performs
prepare/prepublish. Do not publish unless the latest prepublish succeeds.

### 3. Verify only after a new explicit platform completion

Root performs the separately authorized publication in the UI and observes a
new `Deployment Succeeded` for this deployment before invoking verification.
No wrapper performs publication or infers it from an old completion display.

```bash
python -B "$reviewed_runner_dir/pr1921_original_verify.py" \
  --evidence-dir "$reviewed_evidence_dir" \
  --deployment-sha "$reviewed_merge_sha" \
  --actor codex-financial-section-pages-1921
python -B "$reviewed_runner_dir/pr1921_preserve_untracked.py" \
  --evidence-dir "$reviewed_evidence_dir" --label after-verify
```

Verification checks the saved build and prepublish results, raw prepublish
stdout digest, current exact owned lease and returned official verification
identity. The official verifier has no wrapper timeout. Its result must report
three successful probes, a nonempty frontend request list and the prepared
frontend artifact tree. A fresh guard status afterward must be inactive.
The final preservation helper separately checks original nonignored files;
the editor buffer still requires its separate UI observation.

## Required exit and preservation evidence

| Stage | Evidence required for success |
| --- | --- |
| Build | `22-build.result.json`: actual exit integer 0, `problem:null`, command `yarn build`, cwd `/app/frontend`, start/end/elapsed recorded; `outcome.json`: `phase:build`, `ready:true`, exact identities, `lease_created:false` |
| Build preservation | `04-before.manifest.json` is fresh; `24-after-build.preservation.json` has reviewed before/after counts, `changed_paths:[]`, `preserved:true`; `25-guard-after-build.stdout` is inactive |
| Prepare | `26-prepare.result.json` actual exit 0 and no problem; summary `completed:true`, `fresh_owned_active:true`; raw stdout SHA matches summary; fresh before/after status and full owned lease match |
| Prepublish | `27-prepublish.result.json` actual exit 0 and no problem; summary and official stdout `ready_to_publish:true`; exact identity and full saved lease match; raw stdout SHA matches summary |
| Verify | `28-verify.result.json` actual exit 0 and no problem; official result `verified:true`, `checks:3`, exact A/J/M/runtime; nonempty frontend requests; matching frontend artifact tree; fresh final guard inactive |
| Each preservation recheck | `preservation-after-*.preservation.json` and `.summary.json`: matching reviewed counts, `changed_paths:[]`, `preserved:true`; actual underlying `git ls-files` result recorded |
| Unsaved editor | Separate before/after UI evidence for the unchanged unsaved buffer; no filesystem manifest can substitute for it |

Each child result is written before JSON parsing or postcondition assertions.
A wrapper failure, nonzero child exit, missing result, timeout, mismatch or
existing stage record is a stop. Preserve its private evidence. Do not replace
failure evidence with a success summary or delete it to make the stage rerun.
