# Exact B Release Readiness evidence

Read-only inspection of GitHub Actions run 45754802916, attempt 1, completed successfully at 2026-11-26 04:25:58 UTC. No CI rerun, tests, source/Git changes, host actions, or browser actions were performed.

- B: `a59e6ee18f41d13baaa0e863de2ed5810dd43319`
- B tree: `57601ee93838dde732e122354f0766c7e7414301`
- Source A: `b7c7d13f9685fdbc5d7c93b8f8278638bcb2aa6d`
- Runtime: `rg5-3786c214f5c7213c2b3c118ff79d895b716268f479c94d8c5fba2f7a0de66b86`

Both clean-clone rehearsals succeeded: Host Node 24.19.0 job 139691168431 and Host Node 20.20.2 job 139691168436. The absent-input checks, outer frozen install, governed fresh install (Host-install sentinel removed), Git-less adapter build, exact identity/proof assertions, isolated package verification, runtime build-meta JSON response, and clean-worktree checks all passed. The governed build toolchain is Node 22.23.2 and Yarn 1.22.22.

The two downloaded evidence artifacts (15087133310 / Host 24 and 15087461895 / Host 20) match GitHub's SHA-256 digests. Their package proofs are identical: backend 1,390 packaged files, frontend 1,834 packaged files. These are package counts, not test counts. The generated-output artifacts are registered in run-evidence.json; no generated-output archive was downloaded or reused for deployment.

`summary.json` records the examined proof fields. `run-evidence.json` retains the exact run, job/step and artifact metadata. The two bounded log excerpts preserve selected source line numbers. The small evidence ZIPs and extracted environment/package-proof JSON files are retained.

## Guard observation boundary

Neither clean-clone CI job invokes or emits the original-host release-guard status. No `active:false` observation is claimed from CI. The original-host inactive guard remains a separate mandatory operator gate. These successful rehearsals do not authorize a publish or prove production refresh completion.

