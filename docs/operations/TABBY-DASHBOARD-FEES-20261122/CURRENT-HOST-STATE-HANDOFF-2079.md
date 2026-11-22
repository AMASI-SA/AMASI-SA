# PR2079: current host-state handoff
Updated 2026-11-22 UTC. This is an evidence-only document; never deploy this branch.

## Current state
The Tabby implementation and immutable candidate are preserved in https://github.com/AMASI-SA/AMASI-SA/pull/2079 .
Source A d6443504c7d16eba00142526478abf10de336f99; intent-only B d5036d9609b7155ee137d07ff802bbce5be265c5; base J3 02cd99e5b2b68c95341c87590abcf572c885e697.
Candidate/original validators,216 focused backend tests,123 frontend tests, all four required contexts, and both host adapters with independent complete artifact/package proof passed.
Two large required jobs were interrupted simultaneously at21:16:07UTC. Exact cause remains unproven; source and base did not change. Original job logs were unavailable. Attempt1 is retained in the adjacent evidence records, not treated as a pass.
Exactly one targeted retry of each cancelled job was requested at21:19UTC. Attempt2 jobs: Operational137359126218 /run45070030372; Stock137359144844 /run45070030329. All applicable final gates must complete successfully before merge. Do not retry repeatedly or weaken checks.

## Separate host handoff
Latest canonical ledger inspected at21:14:09UTC still reserves the shared host handoff for PR2076. Its actual rehearsal was accepted and its deployment command issued, but staging/prepare/publication/verify3 remain owner-pending. Preserve its retained candidate and every foreign lease. An inactive guard alone cannot establish its completed publication.
No PR2079 merge, shared /app mutation, lease, publication or Production verification has occurred. Publication authorization remains valid; do not ask for permission again.
The local tool environment is separate from the owner's actual Emergent /app.

## Owner evidence needed
Run the following read-only check in the actual terminal and return its output. It prints the full current HEAD/branch/tracked change state and bounded original guard status. A15-second timeout means status is unavailable or an existing operation is holding the mutex; it does not establish inactivity. Do not clear a lease or terminate another operation.

```bash
cd /app
git --no-optional-locks status --porcelain=v2 --branch --untracked-files=no
PYTHONDONTWRITEBYTECODE=1 GIT_TERMINAL_PROMPT=0 python -B - <<'PY'
import json
import subprocess
import sys

try:
    result = subprocess.run(
        [sys.executable, "-B", "scripts/production_release_guard.py", "status"],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=15,
    )
    if result.returncode:
        raise ValueError()
    state = json.loads(result.stdout)
    if not isinstance(state, dict) or type(state.get("active")) is not bool:
        raise ValueError()
except (OSError, ValueError, subprocess.TimeoutExpired):
    print("GUARD_STATUS_BUSY_OR_UNAVAILABLE: no changes attempted")
    raise SystemExit(2)

keys = ("active", "incomplete_lease_directory", "actor", "branch",
        "source_git_sha", "deployment_git_sha", "release_id",
        "protocol_version", "prepared_at")
print(json.dumps({key: state[key] for key in keys if key in state},
                 ensure_ascii=False, indent=2))
PY
```

If PR2076 has completed, also provide the new Deployment Succeeded confirmation and its original verified:true/checks:3 result followed by inactive status. Prefer its existing retained verification result; do not rerun its successful rehearsal/publication/verification.

## Exact next safe action
1. Read the latest task STATUS, current refs, PR2079 exactB gates and canonical ledger.
2. Complete acceptance of the two targeted CI retries. Keep attempt1 evidence visible.
3. Establish that PR2076's serial handoff is released and the actual shared host is clean at J3 with the original guard freshly inactive.
4. Recheck Production exactlyJ3 and PRhead exactlyB. Ordinary merge only, preserving A/B. Validate exact merge parents, Btree equality and original intent/Git validators. If Production advanced, reintegrate/rebuild a new source/candidate rather than deploying the stale intent.
5. Fill the reviewed PR2079 helpers with actual D, then commit the four-helper bundle and separately pin the launcher to its immutable evidence SHA and SHA256s. Review exact constants. D/EVIDENCE/hash placeholders currently remain disabled.
6. Owner executes the reviewed pinned launcher for original isolated adapter, preserved staging and original prepare/prepublish; publishes once after PREPUBLISH_PASSED. Require a new explicit Deployment Succeeded, original matching-own-lease verify3 and exact final inactive status before claiming completion.
