# Environment recovery: PARTIAL / BLOCKED

The latest owner instruction limits this phase to environment recovery and
read-only environment checks. Merge and Deploy are held pending the owner's
review of environment health and completion of the Release Candidate.

No application, financial code, Release Guard, configuration, permission or
write-control changes were made during recovery. No shared runtime process was
killed or restarted. No release lease was created.

| Check | Result | Direct evidence / limitation |
|---|---|---|
| GitHub authentication | PASS | Connector authenticated as AMASI-SA; existing Git credential helper successfully performed the earlier authorized CI rerun without persisting credentials |
| Repository access | PASS | Fresh fetch of source and production refs; PR1243 read back OPEN/Draft at the expected SHA |
| Clean source checkout | PASS | Detached validation worktree clean at source A; tracked source/guard files available |
| Local shell / filesystem | PASS | Workspace and runtime paths exist; temporary create/write/read/cleanup probes succeeded under both Windows Temp and .codex/node_repl |
| JavaScript kernel | FAIL | Minimal nodeRepl.write probe fails before execution: failed to write kernel assets, system cannot find path, os error3; reset did not repair it |
| Browser tool | FAIL | cua.getState fails before browser inventory after reset with the same kernel-assets error |
| Preview | NOT VERIFIED | Existing documented preview origin returned HTTP403 without authentication; no bypass attempted. open_in_codex returned queued, not a loaded page or terminal |
| Shared /app terminal | NOT AVAILABLE / NOT VERIFIED | No terminal attached to this task; browser inventory never initialized. Local Windows checkout is not the shared /app |
| Release Guard on target | NOT VERIFIED | Script exists unchanged. Local Windows --help fails importing Unix fcntl; no port, stub or Guard edit. Must run unchanged on Linux /app |
| Current Production source | VERIFIED GITHUB ONLY | origin/hotfix/prod-snap-meta-final = 83363097d48e034dc7140a60c290efc684e1ffde |
| Current Integration | VERIFIED | HEAD57688c6423848165525bbc85265c4bb56e65da1c; TREE9f5a6f76a827e91602eee994343e5bb68d29f131 |
| Live public health | REACHABLE ONLY | Single read-only GET https://mezansalla.com/api/health returned200, ok=true, protocol5; runtime source967ac7a90b8d798ce8a52a2d62bcf0f985034a9f. Not a Guard verify, new deployment proof, or proof that GitHub's current source is deployed |

The initial /api/health/live probe returned404; that was not the repository's
documented health route and is not classified as an application outage. The
correct existing Guard path /api/health was then inspected.

Prepare, prepublish, deploy and verify remain **NOT EXECUTED / target availability
NOT PROVEN**. Local checkouts and green CI cannot substitute for the shared
workspace's lease status, adapter rehearsal, prepared identity or deployed proof.

The tool reset and filesystem checks did not establish which internal runtime
asset path is missing. The observed diagnostics classify failure_kind as
kernel_assets. Do not claim a specific missing file or restored browser connection.
No global process termination, hand-built helper client or release bypass was used.

Next environment action: restart/reconnect Codex's browser runtime through its
supported application controls, then rerun browser inventory, access the existing
authorized Emergent project terminal and run only the unchanged /app Guard status.
Official browser troubleshooting also describes restarting the app / checking
Computer Use connection and reporting persistent failures:
https://learn.chatgpt.com/docs/chrome-extension#troubleshooting

Existing acceptance work started before the environment-only instruction is
recorded separately. Source-A CI39/39 PASS; setup/public409/physical API subsets
completed; the pre-existing backend/Smoke runner was still in progress when this
report was written. These results do not declare RC readiness. The downloaded
source-A intent candidate remains external and uncommitted; intent-only B and
its mandatory fresh clean-clone adapter rehearsal are not complete.

Production financial writes by this task=0. Write-control UNCHANGED.
Opening=NO; Inventory initialization=NO; Activation=NO; Backfill=NO;
financial schedules=NO; Release lease=NO; Merge=NO; Deploy=NO.
Full Business UAT remains NOT PASS, separate from technical software deployment.
