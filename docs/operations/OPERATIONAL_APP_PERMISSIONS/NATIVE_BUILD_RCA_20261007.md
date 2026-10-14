# Native isolated APK build — BLOCKED, no retry

## Authorized configured-repository download — latest attempt

User authorized restoring exact dependencies from the already-configured
repositories. Removed only --offline from the isolated command; dependency
versions, repository URLs, app source and Java/NDK versions remain unchanged.
Java17 discovery continued to work. The build exited1 after 5m35s at
app:checkDebugAarMetadata and expo-modules-core:compileDebugKotlin.

The actual new blocker is hostname resolution inside Java/Gradle:

```
Could not GET https://repo.maven.apache.org/maven2/.../react-android-0.81.5-debug.aar
This is usually a temporary error during hostname resolution ... (repo.maven.apache.org)
Could not GET https://dl.google.com/dl/android/maven2/.../annotation-jvm-1.7.1.jar
No such host is known (dl.google.com)
```

Read-only Resolve-DnsName after failure successfully resolved both names. This
does not prove the failed Java process had connectivity or that a later build
will succeed. No DNS/proxy/security changes, mirror substitution or follow-up
build was performed. No new APK or installation; Device UAT remains not run.

The isolated synthetic fixture was started at 127.0.0.1:8135 against exactly
operational_balance_device_uat_20261005, replacing only this task's identified
old fixture process. New synthetic owner/actor namespace ob-*; existing fixture
data preserved. Owner/both/write/read/no-permission API preflight all PASS,
including assigned entities and active operational setup. This is API fixture
validation, not real-device acceptance. Prepared UI driver remains unused.

Source remains native05a62fff3ed50be24f6e5cd8f1c91c0c8a06ff42 and product backend
eb4ddb7fd57c13bc9d14f585929f0b74a7f8bc38. Logs in the same evidence directory;
previous offline logs preserved. No app/dependency changes. Production writes=0.

## Authorized Java-path retry — 2026-10-07

After the user identified the existing Java on D:, verified both java and javac
as Temurin17.0.20.1 in
`D:/amasi-build/invoice-b-device-uat/jdk17/jdk-17.0.20.1+1`.
Passed that path and Java21 only to this Gradle invocation through
`org.gradle.java.installations.paths`, with toolchain auto-download disabled.
No global Java configuration, app source or dependency version changed.

The previous Java17 failure is resolved: settings-plugin compileKotlin completed.
Metro also bundled 1550 modules successfully. The single authorized retry then
failed at `:expo-modules-core:compileDebugKotlin`, resolving
`debugCompileClasspath`, exit1 after 2m10s. Offline cache misses include:

- react-android-0.81.5-debug.aar
- kotlinx-coroutines-core-jvm-1.7.3.jar
- kotlinx-coroutines-android-1.7.3.jar
- annotation-jvm-1.7.1.jar
- kotlin-stdlib-jdk8-1.8.20.jar

Selected cache: D:/Android-migrated/gradle-home. Read-only search found the four
listed JARs in D:/amasi-build/gradle-home, but did not find the React Android debug
AAR in either searched cache. No copying/cache switch/download/retry was performed
after this failure. Prepared test fixture files were not started and did not seed
the database. No APK was produced/installed and no device scenario executed.

Evidence: java-toolchain-proof.json, build.log, build-result.json. Prior attempt
preserved as build-java17-undiscovered.log and corresponding result JSON in the
same local evidence directory. Next step requires resolving approved cached
artifacts or restoring the exact existing dependencies from their configured
sources; do not change versions. Stop with RCA per user instruction.

## Previous attempt

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
