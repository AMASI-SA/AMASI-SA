# Completion release operator drafts

**DRAFT — no original-host operation has been executed from this bundle.**
`operator_config.py` has `DRAFT = True`. Every operational entry point refuses
before any host subprocess until root freezes the exact configuration and
independently reviews this bundle. These files are outside governed application
source. They do not alter the official Release Guard.

## Known prior state and immutable chain

The read-only root preflight at 03:23 UTC observed:

- Original guard inactive; tracked checkout clean; 432 untracked files.
- Host HEAD `317dc183485566ba9e05c5afca5d4ea1b84e291e`, tree
  `a8aedd1be45cf7dcb26b31078a6c5e580709e30f`.
- Backend RUNNING, PID 27769; prior restoration timestamp
  `2026-11-26T02:41:22.858577+00:00`.
- Free space 172,650,496 bytes; old generated-output archive 95,817,277 bytes.

The preceding stage is `.git/operational-refresh-2133-50cbe07d`. Its original
receipt remains `production_verified_local_restore_pending`; its separate
continuation proves successful restoration. Neither file is rewritten.

| Evidence | Pinned SHA-256 |
| --- | --- |
| Original `receipt.json` | `0ef141cb905ebd2ca3593cd3bc42d0d78578652e9712637cd6e090816d343d53` |
| Original `guard-verify.json` | `108220774d22e0074127aa10d63b1dbd5e35afbaaf87998ac14c6bed37da6c4b` |
| Separate `restore-continuation.json` | `143827cc19bccacb84fbfea33cf31e8e08f52765275b653460d85f4e3d20f959` |

Validation checks their linked identities, the original single publication and
single successful three-probe verifier, the retained preservation failure, the
separate restored Backend state, and the exact earlier variance records. This
does not rerun the old verifier or publish the old release.

## Files and responsibilities

| File | Responsibility |
| --- | --- |
| `operator_config.py` | Disabled configuration; known old chain and new A/B/D/runtime/stage placeholders |
| `prepare_original_host.py` | Adapted prior operator; fresh inactive guard, prior proof, owned stop, fresh baseline, cache quarantine, source fast-forward and unchanged governed adapter/verifiers |
| `owned_output_backup.py` | Exact dependency subtree reuse and fresh small-output archive under the free-space reserve |
| `operator_preservation.py` | Pinned old chain, complete new baseline, exact two-path observation and separately reviewed variance admission |
| `verify_original_host.py` | One new original verifier invocation after explicit publication; persist official success before preservation/restoration |
| `restore_original_backend.py` | One separately recorded restoration of this attempt's stopped Backend; no original-receipt rewrite |
| `offline_contract_checks.py` | Small synthetic filesystem probes; host subprocesses are forbidden |

## Root configuration before any host use

Fill the exact source A, intent B, merged deployment D, common B/D tree and
deterministic runtime ID after all release gates. Candidate values are not
implicitly accepted: A/B/D remain marked placeholders in this draft. Set
`STAGE_REL` to `.git/operational-refresh-completion-<first eight A characters>`.
Only after independent review, exact bundle hashing and durable checkpoint may
root set `DRAFT = False` in the final reviewed bundle.

Before uploading or changing anything under `/app`, root must freshly run the
unchanged original `production_release_guard.py status` and observe inactive.
Upload the reviewed operator bundle to a **separate task-owned directory** such
as `/app/.git/operational-completion-operator-<A-prefix>`, not the reserved new
`STAGE_REL`: the preparer must create that stage exclusively. Keep all imports
together. Do not put operator files in the 432-file untracked application cohort
or overwrite a prior stage. Confirm uploaded file hashes against the reviewed
bundle before executing an entry point.

The preparer's first host subprocess is another original guard status read.
It creates no lease, performs no publication and runs no financial calculation.
It requires the current clean base and correct prior runtime, stops only the
observed RUNNING Backend, and then captures a fresh baseline. Existing ignored
foreign roots are protected by metadata; noncredential untracked files retain
their inherited content-digest policy. Credential paths remain metadata-only.
The explicit Backend credential baseline also includes size, mtime and nlink.

Every later preservation comparison validates the full ordered **59-root and
432-file** cohort against the pinned old record, a complete 432-record manifest,
required content hashes, and the complete metadata-only credential baseline.
A truncated or reordered baseline cannot make preservation appear successful.

## Space-bounded owned-output backup

The observed free space exceeds the 128 MiB reserve by only 38,432,768 bytes;
another 95,817,277-byte full backup would not fit within that reserve.
The proposed path therefore does not duplicate all dependencies:

1. Inventory all four owned generated roots without following symlinks. Reject
   protected configuration names and any regular-file hardlink escaping the
   inventoried generated scope before opening payloads. Check the explicit
   Backend credential inode alias as well.
2. Verify the entire old immutable archive against its pinned receipt digest.
   Compare its complete `frontend/node_modules` subtree with the current one:
   member paths, file/directory/symlink types, permission modes, uid/gid,
   symlink targets, hardlink equivalence classes, sizes and content SHA-256.
   Current dependency hardlinks outside that subtree are rejected using nlink
   and inode-group counts **before any current dependency payload is opened**.
3. Reuse the old dependency backup only if the complete comparison matches.
   Fresh inode/mtime/nlink metadata is retained in a new compressed inventory;
   old tar inode/mtime values are not substituted for that fresh metadata.
4. Create a new archive of only `frontend/build`, `frontend/.release` and
   `backend/release_identity.json`. Compressed inventory/archive writes retain
   the 128 MiB free reserve. Verify complete membership, stream CRC, unchanged
   input inventory, and saved archive digest before advancing source/build.

No old stage, backup or cache is deleted. The backup helper removes no current
generated output. Only the unchanged governed adapter later removes its normal
owned output paths. Reuse mismatch or insufficient reserve stops preparation;
there is no fallback deletion or blind retry. Partial new files remain in the
exclusive new stage for inspection.

The new receipt records a composite restoration recipe: the immutable dependency
payload reference plus the fresh metadata inventory and new small-output
archive. It must not be mistaken for the earlier single-full-archive format.

## Publication and verification sequence

After preparation reaches `local_verified`, root performs the existing reviewed
Release Guard prepare/prepublish and exactly one separately authorized provider
publication. This bundle has no publishing entry point. Root records these new
receipt fields only after observing newer explicit completion:

- `phase = publication_succeeded`
- `publication_action_count = 1`
- `publication_version` distinct from the prior `bad38ff`
- `publication_succeeded_at`, timezone-aware and after `local_verified_at`

The verifier requires the exact active v5 actor/source/deployment/runtime guard
and clean checkout, records `verify_invocations = 1` before admission, and runs
the original verifier unchanged once. It joins only its own process group on
interruption. It persists the exit code and official three-probe proof, then
requires a fresh inactive guard before any Backend restoration.

If preservation is strict, restoration proceeds through a separate continuation.
If preservation differs, official success is retained and a separate
`postpublication-preservation.json` binds the exact observation to the final
original receipt bytes and official proof. No local restart or waiver occurs
merely because a difference involves a known path.

## Only two reviewable post-publication variances

| Exact path | Eligibility for independent exact-observation review |
| --- | --- |
| `backend/.env` | Regular file, same device/mode/uid/gid/size/nlink, different inode; mtime recorded. No contents are opened, hashed, copied or asserted equal. |
| `.emergent/stripped_env_names` | Same metadata identity and nlink, positive append of at most 4,096 bytes, old prefix SHA-256 preserved exactly; full before/after metadata and hashes recorded. |

Actor attribution remains **not established**. Any other path, changed prefix,
truncation, link-count change, missing file or incomplete baseline is refused.
Root may independently review an eligible observation and create a separate
private `reviewed-preservation-approval.json` containing the exact fields below;
the wrappers never create an approval:

```json
{
  "schema": "operational_preservation_exact_approval_v1",
  "reviewed": false,
  "actor": "operational-refresh-completion-20261126",
  "source": "EXACT_A",
  "deployment": "EXACT_D",
  "release_id": "EXACT_RUNTIME_ID",
  "original_receipt_sha256": "EXACT_RETAINED_RECEIPT_HASH",
  "official_guard_proof_sha256": "EXACT_NEW_OFFICIAL_PROOF_HASH",
  "preservation_observation_sha256": "EXACT_SAVED_OBSERVATION_HASH",
  "approved_variances": [],
  "variance_actor_attribution": "not established",
  "credential_contents_verified": false
}
```

Root sets reviewed true only after reviewing the exact observation, and copies
the exact eligible variance objects into the approval. Restoration rechecks
the current observation, approval binding, official proof, unchanged checkout,
old evidence chain and current STOPPED service before starting Backend once.
It preserves the original receipt unchanged and records service success or
failure in `restore-continuation.json`. A continuation already present requires
inspection, never automatic replay. A failure after the immutable observation
is retained separately as `postverify-wrapper-failure.json`.

## Local checks and remaining gates

All six operator Python modules parsed successfully without executing them.
`PYTHONDONTWRITEBYTECODE=1 python -B completion-release-prep/offline_contract_checks.py`
exited 0 with **21 small synthetic checks**. Results are preserved in
`offline_contract_checks_result.json`. Tests used only owned temporary trees;
all subprocess functions were replaced with rejection functions, and credential
content opening during preservation inspection was explicitly forbidden.
Coverage includes both hardlink-mismatch directions, external-link rejection
before payload reads, valid matching linked/unlinked trees, changed bytes,
strict/eligible/ineligible preservation, exact approval mismatch, incomplete
baselines and DRAFT refusal before any subprocess.

These checks do not prove current host dependency equality, sufficient fresh
archive space, a governed build, publication, live financial freshness or data
preservation after the next calculation. Root still owns final constant freeze,
independent review, documentation-only durable operator checkpoint, fresh host
preflight, all original release gates and live financial acceptance.
