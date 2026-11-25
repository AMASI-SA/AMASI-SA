# PR2123 original Release Guard v5 sequence — preparation only

This file documents the original CLI from `scripts/production_release_guard.py`. No command below has been executed by this preparer. Root alone prepares, publishes and closes the release after the rehearsal and release gates pass.

## Fixed identity

- Actor accepted by root: `chatgpt-operational-cost-order-projection-20261124-pr2123`.
- J / source base: `f199a471f8bc6e05b475bb9b65a12107cbdc946b`.
- A / source: `e0726036bbfaa524d55f80dfea9f2a8f9facdbfe`.
- B / intent: `c71a0ba71e36a2306a0c4bce1822f39073e6984f`.
- D / deployment: `6323017fbf9ba160c536a94f408507435786412a`.
- RID: `rg5-d5df5e9536258c2d3b76d750715bafae8fa9b6b76f6971899bd7f8c6e0e54328`.

## Original CLI and order

Run each original command with working directory `/app`. `-B` is a Python option; the guard flags below are its actual argparse interface.

1. After successful rehearsal and all prerequisite release gates, prepare exactly one lease:

   ```sh
   python -B scripts/production_release_guard.py prepare --actor chatgpt-operational-cost-order-projection-20261124-pr2123
   ```

   Preserve the exact returned lease and the raw `/app/.git/mezan-production-release.lock/lease.json` bytes. Root must validate the fixed identity and record the actual `prepared_at` and raw-byte SHA-256. These values do not yet exist in the draft.

2. Immediately before the authorized platform publication, run the original prepublish check:

   ```sh
   python -B scripts/production_release_guard.py prepublish
   ```

   Require successful exit and matching `ready_to_publish: true`, source SHA, deployment SHA, RID and protocol 5. Publication is the separate platform action; the guard has no publish subcommand.

3. After that publication completes, invoke the original verifier once through the independently reviewed, lease-bound capture wrapper:

   ```sh
   python -B scripts/production_release_guard.py verify --url https://mezansalla.com
   ```

   The CLI command is `verify`, not `verify3`; there is no `--checks` flag. `_verify_locked` performs three complete health/frontend observations internally, separated by two-second pauses. It checks the original prepared time and identity, then removes only the matching unchanged lease while holding the original operation mutex.

4. The capture wrapper runs original status after a matching successful verification:

   ```sh
   python -B scripts/production_release_guard.py status
   ```

   Require `active: false`, no `incomplete_lease_directory`, and absence of the lease directory. Status records closure; the preceding successful original verify performs closure. Do not invoke verify/status separately a second time around the capture wrapper.

## Binding the draft later

`pr2123-original-verify-DRAFT.py` exits before any `/app` read, stage write or child command while `LEASE_SHA256` or `PREPARED_AT` remains unbound. After original prepare, root makes a separate finalized copy with those two literal values taken from the exact actual lease, then obtains independent review of the limited diff and exact bytes. The existing draft remains historical evidence.

The final wrapper expects one private real directory `/tmp/mezan-pr2123-verify-*`, owned by the executing user with mode 0700, containing `original-lease-before.json` with the exact raw prepared-lease bytes. It refuses reuse of an existing `receipt.json`. Run the finalized wrapper once without Python optimization; its child environment removes PYTHONOPTIMIZE/PYTHONHOME/PYTHONPATH and sets PYTHONDONTWRITEBYTECODE=1.

Frontend artifact SHA comes from the digest-pinned original lease. Expected request count comes from the same lease's public file paths and the original v5 rule: the unique paths `/build-meta.json`, `/`, `/snapchat-accounts`, and each prefixed public path receive two cache variants, with two Accept variants for `/build-meta.json`. The wrapper does not assume PR2120's former artifact SHA or request count.

The prior capture limits remain 3600 seconds for original verify and 45 seconds for original status. Failure retains the capture evidence and does not retry, prepare, abort, publish, manually remove a lease or roll back. A missing/incomplete receipt is not proof of success.
