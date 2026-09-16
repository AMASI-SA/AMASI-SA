# Preview reviewer sign-out — 2026-09-16

Task: PREVIEW-REVIEWER-SIGNOUT-20260916; supports MZ2-FIN-CUTOVER-001.
Status: PREVIEW_LOGOUT_VERIFIED_AWAITING_OWNER_SIGNIN.
Scope: isolated Preview frontend only. Production changed: NO.

## Defect and change

The authenticated Meta reviewer cannot access the normal sidebar (the All button is
intentionally disabled) and /profile redirects to Meta integrations. The only existing
logout button is in that inaccessible sidebar. Browser reproduction confirmed no usable
logout control. A direct gateway URL returned a bare 403 and was not retried.

Add a visible, accessible reviewer-only logout button to the established navigation
shell. It calls the existing AuthContext logout function. Administrative navigation,
routes, account permissions, backend authentication, existing credentials and owner
navigation remain unchanged. This is normal logout, not session fabrication or an
account-creation workaround.

Reviewed Preview runtime base: 9f8b16998f62cbdb08dc9361bc2e443223d27d17.
Latest repair checkpoint: 89141bc435cb1a6fb8d5333c141ad1dcb8fe4e92.
Source branch: fix/preview-reviewer-signout-20260916.
Existing runtime: /opt/mezan-preview-runtime-20260916.
New isolated frontend snapshot: /opt/mezan-preview-signout-20260916/frontend.
No /app files changed. Independent code-server workspace and newly created Terminal 1
used; existing deployment terminals and leases untouched. Fresh guard status active:false;
frontend PID117486 and backend PID130800 running; /app tracked diff empty.

## Verification

Focused regression against unchanged source: exit1, 1 failed / 1 passed.
Failure is missing button[aria-label="تسجيل الخروج"], as intended.
After patch: exit0, 26 tests passed across all five MezanV2NavigationShell suites.
Tests exercise reviewer signout delegation, disabled full navigation, absence of admin
links, and preserved owner menu behavior, alongside existing navigation regressions.

Command, from the isolated frontend:
```sh
CI=true NODE_ENV=test node node_modules/react-scripts/scripts/test.js --watchAll=false --runInBand --runTestsByPath src/components/MezanV2NavigationShell.signout.test.jsx src/components/MezanV2NavigationShell.test.jsx src/components/MezanV2NavigationShell.fulfillment.test.jsx src/components/MezanV2NavigationShell.employees.test.jsx src/components/MezanV2NavigationShell.adCosts.test.jsx
```

Patched component SHA256:
587c608478f3cec7caa7ae4bb429d9b2f97de8a85fab4a2b81c0805abbd73b9b.
Unchanged AuthContext SHA256:
be053f25d59857f80754de9d6e19fc354d2063f6e3919046b1e394a6b80d7bd5.

## Next safe action

Build the isolated snapshot with the exact Preview API origin, retain explicit
Preview metadata without claiming a Production release identity, then switch only
the frontend Supervisor section after comparing it with the preceding repair's
recorded section. Preserve rollback and all unrelated service sections.
No merge or Production publication. Actual browser logout/login remains unverified.

After logout, present the normal secure login form for the owner. Verify/create
the independent synthetic Viewer before granting settlement-view-only access.
The first synthetic employee remains linked by the user's manual action to the
existing Meta reviewer; do not unlink/disable the reviewer as a test shortcut.
No live posting403 or accounting gate completion is claimed.

## Preview activation and browser acceptance

The isolated Vite build exited0 (5.54s). Existing warnings about CSS import placement,
large chunks and mixed imports remain; they did not prevent the build.
Served index SHA256: 2e377ff8aa4ac3910c1f9d70938f26d1161a3afbef5b9f5007cf654c19a7693c.
The explicit preview-meta.json identifies source_patch_git_sha
497311cd14208303730f5dfadac94c4d6b1c9d48 and the exact Preview API origin.

Immediately before switching, the release guard was inactive. Compared the current
frontend Supervisor section byte-for-byte with the previous repair record, saved
before/after sections in the new runtime root, then changed only that section.
Unrelated sections were byte-identical. The first immediate HTTP probe raced service
startup and got connection refused; no repeat switch was attempted. A subsequent
status/HTTP check confirmed frontend RUNNING PID156124, unchanged backend PID130800,
and the correct served metadata. Shared /app tracked/staged diffs remained empty.
Runtime evidence: /opt/mezan-preview-signout-20260916/signout-verification.json.

Fresh browser reload showed the new logout button while the full menu remained
disabled. Clicking logout navigated to the actual Preview /login and exposed the
email/password form. Thus real browser logout is verified. Owner sign-in and
independent Viewer acceptance remain pending. No account credentials were read,
changed or stored by the agent, and no financial posting or Production change occurred.

The previous repair's frontend rollback command is superseded by this new section.
To roll back ONLY this task, first compare the current frontend section against
/opt/mezan-preview-signout-20260916/frontend-section-after.txt, refusing if different.
Replace only that section with frontend-section-before.txt in the same new root,
then supervisorctl reread and supervisorctl update frontend. This returns to the
previous working Preview frontend, whose reviewer logout defect is still present.
Never replace the full Supervisor config or use the initial outage rollback.
