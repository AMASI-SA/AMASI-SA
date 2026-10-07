# Native isolated APK build — BLOCKED, no retry

Native source HEAD: 05a62fff3ed50be24f6e5cd8f1c91c0c8a06ff42.
Native tree: 71a25926210a4a320f166cc269bc098c282e25c1.
Companion source HEAD: eb4ddb7fd57c13bc9d14f585929f0b74a7f8bc38.

All committed native source files used by the app were materialized from Git into
the existing isolated staging project. Preview-only overlays: app identity,
disabled OTA, source identity and loopback API http://127.0.0.1:8135/api. General
and unrelated scoped writes disabled; operational switch enabled for test only.
No package/dependency versions changed. No APK was installed or tested this turn.

Existing NDK27.1.12297006 has source.properties and clang.exe. Gradle ran on the
existing Microsoft Java21 installation, with English locale, ARM64 only, offline,
using the prior short-path native staging configuration. It failed before app
compilation, exit1 in 59 seconds:

```
Could not determine the dependencies of task ':gradle-plugin:settings-plugin:compileKotlin'.
Cannot find a Java installation ... matching: {languageVersion=17, vendor=any vendor, implementation=vendor-specific, nativeImageCapable=false}.
... No cached resource ... available for offline mode.
```

React Native's existing settings-plugin/build.gradle.kts explicitly requires
`kotlin { jvmToolchain(17) }`. Java21 running Gradle does not satisfy that compiler
toolchain requirement. The isolated environment did not expose a recognized
Java17 installation and offline mode refused automatic provisioning. This is an
environment blocker, not an observed app runtime failure or NDK defect. The
machine-wide absence of Java17 is not asserted.

Codex Process Jobs first refused the Windows platform without launching the build;
the supported local command runner was then used. No policy bypass or download.
After the build failed, no retry, toolchain repair, installation or device action
was performed. Per user instruction stop with RCA before repair.

Durable local logs and exact source manifest:
`D:/codex-evidence/operational-device-20261007/{build.log,build-result.json,source-proof.json,build_candidate.py}`.
Samsung SM-G975U was connected, but no new APK/device UAT PASS is claimed. The old
APK remains the old candidate. Next repair, once authorized: locate and validate
an existing approved Java17 toolchain and make it discoverable only in the
isolated build; do not change React Native, dependencies or Production.

Local code verification remains: full backend254 PASS, final focused13 PASS,
affected Web61 PASS, full native typecheck/verifier chain PASS including 21
permission cases and 14 case-specific checks. Feature gaps remain in
NATIVE_CASES_20261007.md; this is not final implementation acceptance.

Production unchanged by this task. Production writes=0. No merge, deploy,
Prepare/Prepublish, Accounting or changes to #1263.
