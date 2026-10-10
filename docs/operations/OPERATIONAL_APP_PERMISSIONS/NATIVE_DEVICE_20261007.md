# Isolated native UAT — historical build/infrastructure record

Superseded by `ANDROID_UAT_20261007.md`: emulator recovered with cold boot and
software rendering; actual functional tests executed. Historical blockers below
are retained as evidence and do not describe the current emulator state.

Native source: `05a62fff3ed50be24f6e5cd8f1c91c0c8a06ff42`.
Native tree: `71a25926210a4a320f166cc269bc098c282e25c1`.
Backend product source: `eb4ddb7fd57c13bc9d14f585929f0b74a7f8bc38`.

The previous build environment blockers are resolved. Existing Java17/Java21
and NDK27.1.12297006 were retained. IPv4 preference was set only for the isolated
Java invocation after repository connection probes; no global network/security
configuration or dependency version changed. ARM64 assembleDebug succeeded in
16m27s, 641 tasks. Version1.0.11/code14, package
`com.amasi.sa.operationalpreview`, OTA disabled, test-only configuration.

ARM64 APK SHA256:
`d69a68eea0a4e21f7f8b745dde865267e6356606923e561fbf0787ffed520b22`.
Samsung SM-G975U/API31 installation succeeded and installed base.apk hash matched.
Phone disconnected before successful test login; no financial Device UAT PASS.
The synthetic fixture's missing mobile refresh route was corrected in the local
test harness only. Five fixture/API permission combinations passed; this does
not establish native UI behavior or Production authentication compatibility.

User then requested the existing emulator. Android15 emulator reports
`x86_64,arm64-v8a`; ARM64 install succeeded but runtime failed before login with
SoLoaderDSONotFoundError for libreactnative.so. The APK contains only the ARM64
library while DirectApkSoSource searches lib/x86_64. Screenshot and crash log
preserved. User authorized continuation; an x86_64 build from the same exact
source succeeded in 5m54s (641 tasks). No app source repair or dependency change.

x86_64 APK SHA256:
`e89bda291f02a2a5dbcb251a8c439c62476b4f7193b77ab6a801441e2e12b39c`.
Embedded JS bundle SHA256 is identical to the ARM64 artifact:
`0469bdc4d46879a466c5e82f3c5ffaabbee4f61e58df424eee84d73776b23f87`.
ADB install returned Success. App splash was visible; the previous native-library
failure was not observed on this launch. This does not prove successful login.
Pixel Launcher ANR and inconsistent window focus interrupted UI automation.
The emulator process restarted at 20:35:46 local time without this task issuing
a restart, and ADB transport changed. Afterwards even a bounded shell echo timed
out after ten seconds. The user was asked whether another test controls it.
No restart/kill of the shared emulator was attempted by this task.

Latest result: build PASS; installation PASS; synthetic fixture preflight5 PASS;
native functional UAT BLOCKED (no completed login or movement scenario claimed).
Screenshot emu-x86-current.png shows the app splash. This infrastructure blocker
must not be counted as a new proven Operational Balance product defect.

Local evidence: `D:/codex-evidence/operational-device-20261007/`, including
build-result.json, source-proof.json, emulator-crash.log, emu-05.png and
emulator-x86_64/build.log. Test backend is loopback port8135 via ADB reverse,
database `operational_balance_device_uat_20261005`, synthetic users/data only.
External backend connections and accounting imports are blocked by the fixture.

Next: obtain a responsive exclusive emulator session, run native UI scenarios and record
PASS/FAIL/BLOCKED individually. Stop for RCA on an observed product failure.
Known feature gaps remain in NATIVE_CASES_20261007.md. Production writes=0;
no merge, deployment, release operations or modifications to frozen #1263.
