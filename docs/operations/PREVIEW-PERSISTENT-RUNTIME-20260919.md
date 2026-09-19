# Preview persistent runtime recovery — WIP

Scope: isolated Preview only. Production is not modified by this task.

Observed failure: Supervisor retains commands under /opt, while the private backend launcher and frontend directory are missing. findmnt reports /app on its own ext4 mount and /opt on the container overlay. Both app services are FATAL with ENOENT. This demonstrates a runtime path persistence mismatch; it does not prove which actor recreated the container.

Candidate script: scripts/preview_persistent_runtime.py. Pinned whole application source: e82918d130f42b228346b2cbd3d70ff4daf21bf3. Prior reviewed auth boundary hashes match this candidate. Runtime artifacts and private Node toolchain are relocated under an isolated /app/.worktrees directory. Dependency installation uses a disposable /tmp Yarn cache while installed dependencies remain persistent. External network is denied in the application process and automatic workers are disabled. No old parser overlays are installed. Supervisor changes are restricted to backend/frontend command and cwd, with compare-before-write and release-guard checks.

Verification: script compiles; candidate source fetched and verified; auth hashes matched. Initial shared dependency copy failed after the shared node_modules disappeared. Private dependency install then failed ENOSPC. Only generated files belonging to these failed attempts were removed, with package download cache moved off the persistent volume. A subsequent fresh preflight stopped with Release active before rebuilding. Activation, runtime boundary verification, full build, browser acceptance and restart test have NOT passed. No service configuration or financial data was changed by this attempt.

Resume only after current release owner closes the lease. Re-read AGENTS, guard status and reservations. Ensure this task owns its Preview reservation, inspect state before preparing, run prepare, verify, then activate only while the guard remains inactive. Capture local DB financial counts/digests before activation and compare after. Verify both runtime identities, gateway reachability, denied non-Preview host/origin, and restart persistence. Preserve the prior three settlements. Existing local Mongo topology still requires verification before financial acceptance.

Do not merge this tool as a Production feature. No P01 closure claim.
