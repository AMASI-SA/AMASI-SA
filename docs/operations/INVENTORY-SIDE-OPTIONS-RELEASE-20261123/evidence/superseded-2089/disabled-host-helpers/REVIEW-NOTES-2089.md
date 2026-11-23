# PR2089 disabled host-helper bundle

This directory is isolated preparation, not a deployment. All executable entry points remain disabled because the actual ordinary merge D is not known. Do not substitute the PR2086 D or a synthetic SHA.

## Frozen release pins

- J: 76efd2c3aa1563939ee3e27a9ae03f27fc1a3d43
- A: 45c8fb4ea91e5af8523971fbfa2270ee96552224
- B: 499b98735b5cc4eae79d380e4f8d5ec5477175e5
- B tree: 4816e3468b3fcbcde78ab799760201fcfb2e7171
- Runtime: rg5-a762a47336e81b005a6fdfe509695c01a46b991478e27d6dc421913e94c94762
- Frontend artifact: 5895551a52ba294d373076988d0d1ba75e39855ab28cbd7a7a76c0a4038834e4
- Actor: codex-inventory-side-options-2089-20261123
- Task branch: codex/inventory-side-options-release-20261123
- Production branch: hotfix/prod-snap-meta-final
- Evidence task directory: INVENTORY-SIDE-OPTIONS-RELEASE-20261123

## Exact adaptation boundary

The four operational helpers, launcher and self-test preserve the reviewed PR2086 logic. Only release pins, actor/task names, filenames, receipt kind and private temporary-directory prefixes change. Unknown D and downstream immutable helper/launcher pins are literal None and fail closed.

The bootstrap additionally imports re and validates the exact repository/task URL shape, full SHA256 and bounded integer byte count before any original-guard command or network call. Its URL, digest and size remain None. Reference originals are retained as .txt solely for byte-level review; never execute or deploy them.

The manifest records simultaneous replacements to avoid confusing the previous D with our new J: both have the same SHA, but only our new J is configured.

## Safety preserved

- Original /app protocol-v5 guard, operation mutex and exact own-lease fences.
- New private receipt bound to J/A/B/D/tree/runtime/artifact/actor and adapter-log digest.
- Full original package-boundary proof equality.
- Verified bytes/modes in both external backups and same-filesystem retired outputs.
- Preservation of unrelated untracked files, .env sidecars, dependency root and recognized historical bytecode.
- Ordinary protected fast-forward with --no-overwrite-ignore.
- No automatic rollback, cleanup, foreign-process termination, lease abort or publication.
- Re-entry fails on changed shared HEAD, lease or remote tips; inspect the retained phase rather than rerun blindly.
- Owned rehearsal process-group cleanup only; no unrelated process group is signalled.

## Activation sequence after all B gates

1. Observe a fresh original inactive guard and stable reviewed J; perform the authorized ordinary merge separately.
2. Independently verify actual D parents are [J,B], D tree equals the frozen B tree, and A-to-D changes only the intent.
3. Pin D in the four operational helpers and launcher (five substitutions only), then run independent review and local self-tests again.
4. Commit the reviewed four operational helpers to a separate evidence commit E; calculate their hashes from E.
5. Set the launcher EVIDENCE and four helper hashes; commit the reviewed launcher as L.
6. Set bootstrap URL to the exact raw L file, SHA256 and byte count. Independently review that chain.
7. Preserve E/L/identities/test results in the task status and Issue1006 before actual host execution.
8. Actual host use still requires the fresh original guard and all original helper preconditions. The launcher stops after prepare/prepublish; publication remains a separate single UI action.
9. Use the printed new private verify-after-success.sh only after the new Deployment Succeeded. Do not reuse any prior PR2086 path.

A capture wrapper is deliberately not pre-pinned to a guessed host directory. If needed after staging, derive it from the observed PR2089 private directory and exact own verification-wrapper digest, review it, and retain its full output.

## Local validation scope

The adapted original self-test operates only on disposable fixtures, fake guards and its owned child processes. Its ignored-file-collision test creates and removes a temporary fixture Git repository; those fixture commits are explicitly authorized. No actual source/evidence/production Git commits, host /app access, original guard action, builds, pushes, merges or publication are part of preparation.

Final fresh command results and file hashes are recorded separately after validation.
