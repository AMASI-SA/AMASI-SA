# PR #2122 guarded rehearsal evidence

This evidence branch preserves the reviewed operator for
`DASHBOARD-SPLIT-PROFIT-20261124`. It is separate from the Protocol-v5 source and
intent branch. Do not merge these evidence files into the frozen release
candidate.

## Reviewed source and operator

- Source A3: `10bb33957c8c2994814a76ba5feed1182336ebda`.
- A3 tree: `0941ce9c29e3515346e2f31eac161e9986730e68`.
- Reviewed production base J: `f199a471f8bc6e05b475bb9b65a12107cbdc946b`.
- Release candidate: [PR #2122](https://github.com/AMASI-SA/AMASI-SA/pull/2122).
- Evidence branch: `codex/dashboard-split-profit-evidence-20261125`.
- Operator SHA256:
  `98719b39a298c98c5a56d517bcdf2edd3f350497e53bd05aadf4202a2d43c65b`.
- Original PR #2092 operator SHA256:
  `eeab2c969ef65e1220ddf1e61865c66377d4e0089fb451f566196d50811de1d8`.

The adaptation changes only the exact J constant, PR/task/import labels,
private-stage prefix, and removal of inherited
`MEZAN_RELEASE_BOOTSTRAP_INTENT` from the adapter subprocess environment.
The original operation lock, preservation checks, backup/fsync behavior,
subprocess argument structure and timeouts remain unchanged.
`adaptation.diff` records the complete difference.

Independent review passed with one required invocation condition:
**set `PYTHONOPTIMIZE=0` explicitly**. The unchanged intent and canonical-proof
checks use Python assertions. Do not run Python with `-O` or `-OO`; keep the
reviewed operator bytes unchanged.

## Isolated verification

The retained checks passed all five groups, exit 0, in 1.053 seconds:

1. All four generated-output backups preserve bytes, modes, literal symlinks
   and fsync receipts/stage entries.
2. Only a same-file, prefix-preserving runtime-metadata append is accepted.
3. Prefix corruption and byte-identical inode replacement are rejected.
4. A nested mount is rejected independently of the device number.
5. A timed-out process group, including a descendant ignoring SIGTERM, is
   terminated and a failed receipt is retained.

The checks execute the helper module under a non-main name and substitute a
temporary fixture root. They do not invoke `main`, the protected rehearsal,
the actual `/app`, a release guard, Git/network operations or publication.
The logged result and `verification.json` bind the exact helper and test bytes.

From the repository root, the isolated checks can be repeated with:

```bash
PYTHONOPTIMIZE=0 PYTHONDONTWRITEBYTECODE=1 python -B \
  docs/operations/DASHBOARD-SPLIT-PROFIT-20261124/verification/operator/ISOLATED_CHECKS.py \
  docs/operations/DASHBOARD-SPLIT-PROFIT-20261124/operators/pr2122_guarded_rehearsal.py
```

## Actual rehearsal prerequisites and invocation

No actual rehearsal, shared-host action, lease or publication has occurred as
part of this evidence work. The A3 governed build and new intent-only B remain
pending at this checkpoint. There is no reviewed execution envelope yet.

The owner must first complete the A3 build and intent freeze, verify exact-head
CI/review, and obtain the normal merge D whose parents are exactly J then B.
The previous publisher must complete its own release and verification; its
operation lock or lease must never be cleared, borrowed or bypassed. Wait for
the running original guard operation to finish before making another guard
call. Confirm the current public identity and inactive original guard before
any shared-workspace mutation.

Only after exact D exists, its CI has passed, and a fresh count of the current
foreign untracked entries is known may the owner create the reviewed envelope.
It must contain exactly these seven fields, with no placeholders:

| Field | Required value |
| --- | --- |
| `source_sha` | Full reviewed source A SHA |
| `source_base_sha` | `f199a471f8bc6e05b475bb9b65a12107cbdc946b` |
| `intent_sha` | Full intent-only B SHA, whose sole parent is A |
| `deployment_sha` | Full exact D SHA, whose parents are J then B |
| `tree` | Full common B/D Git tree SHA |
| `release_id` | Exact reviewed `rg5-` runtime identity from the frozen intent |
| `expected_untracked_count` | Fresh integer count from `git ls-files --others --exclude-standard -z` |

Stage the exact operator and reviewed envelope in an owner-created private
directory outside `/app`. Assign that existing directory to
`PROFIT_OPERATOR_DIR` and verify the operator SHA256 above. Run from the real
`/app` root only after all prerequisites pass:

```bash
cd /app
PYTHONOPTIMIZE=0 PYTHONDONTWRITEBYTECODE=1 python -B \
  "$PROFIT_OPERATOR_DIR/pr2122_guarded_rehearsal.py" \
  "$PROFIT_OPERATOR_DIR/reviewed-envelope.json"
```

The helper serializes under the original guard operation lock, checks the
inactive lease and exact clean J checkout, preserves foreign untracked entries
and prior generated outputs, fast-forwards only to reviewed D, and runs the
unchanged official `yarn build` adapter from `frontend` using the reviewed public
origin. It does not create a lease or publish. A blocked result retains its
private receipts/backups for owner review without automatic rollback or resume.
Release preparation, prepublication and live verification remain separate
owner-controlled actions after a successful rehearsal.
