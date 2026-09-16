# Preview reviewer sign-out — 2026-09-16

Task: PREVIEW-REVIEWER-SIGNOUT-20260916; supports MZ2-FIN-CUTOVER-001.
Status: SOURCE_VERIFIED_AWAITING_PREVIEW_BUILD_AND_SWITCH.
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
