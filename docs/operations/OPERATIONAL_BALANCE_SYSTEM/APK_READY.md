# OPERATIONAL_BALANCE_APK_READY

An isolated x86_64 Android emulator APK was built successfully and inspected without installing or launching it. Device UAT remains BLOCKED INFRASTRUCTURE; final review remains BLOCKED.

## Candidate and artifact

- Native HEAD: `641537fc45961b31f268eae2bedd4c3356c20bfa`
- Native TREE: `94ec30ba7a9b7cbe8317d18f1ecde7211ee3100b`
- Backend checkpoint used by the local fixture: `f9fbc13b88b332251b2ef16931fc687c9ea604a1` / tree `ea2973d0d152e8bce07a3dbd56e161e8e4a4836d`. Subsequent APK evidence commits change documentation only.
- File: `D:/CodexBuilds/operational-android-20261005/artifacts/AMASI-Operational-Preview-641537f-x86_64.apk`
- SHA256: `57300f6040900dd6bf9eb78108facd5f4f410d3d307a57b93787bb791dbbc48b`
- Size: 81,886,437 bytes.
- Application ID: `com.amasi.sa.operationalpreview`; label: `AMASI Operational Preview`.
- versionCode: `14`; versionName: `1.0.11`; minSdk: 24; targetSdk: 36.
- ABI: x86_64 only. This artifact is intended for the isolated emulator, not an ARM phone.

## Isolation evidence

The tracked app/src/assets files match the native candidate: 120 checked, zero missing or different. No financial or native application source was changed during APK preparation. The build copy has documented packaging overlays: separate Android package/name/scheme, disabled OTA, removed EAS project metadata, embedded JavaScript, and local HTTP permission. Expo prebuild also changed package.json android/ios command aliases, without changing dependencies. Verification scripts copied earlier into the build directory are not runtime inputs; the candidate's 15 verifier passes remain recorded separately.

The packaged JavaScript contains `http://10.0.2.2:8135/api` and the isolated environment marker. The APK scan found no `mezansalla.com` origin, original production OTA project ID, private-key markers, environment files, keystores, or google-services.json. Its one PEM asset is the unchanged public Expo root certificate, verified byte-for-byte against the dependency, not a credential. No production credentials or production data were copied into the build. The API client has no production fallback URL.

The binary Android Manifest confirms the separate application ID and `expo.modules.updates.ENABLED=false`. `apksigner verify --verbose --print-certs` exited 0 and verified APK signature scheme v2 with an Android Debug certificate, not a production signing key.

The only configured backend is the local synthetic fixture listening on `127.0.0.1:8135`, reached through emulator host alias `10.0.2.2`. It uses local Mongo `127.0.0.1:27305`, database `operational_balance_device_uat_20261005`, owner `preview-owner`, and actor `preview-staff`. General application writes are disabled; the operational movement switch is enabled for this test backend. The fixture imports the operational router/worker directly, without the production server or deployment environment. Test data remains local. The backend must be running for later UAT.

## Build evidence and environment repairs

Final command: `gradlew.bat :app:assembleDebug --no-daemon --max-workers=2 -I <task-root>/fresh-native-staging.gradle -PreactNativeArchitectures=x86_64 -Pkotlin.compiler.execution.strategy=in-process -Dorg.gradle.internal.http.connectionTimeout=15000 -Dorg.gradle.internal.http.socketTimeout=15000 --info`.

Microsoft OpenJDK 21 was used with `JAVA_TOOL_OPTIONS=-Duser.language=en -Duser.country=US -Dfile.encoding=UTF-8`. Gradle cache is `D:/obg-20261005`; native staging is `D:/obc-final-1005`. Generated app Gradle configuration sets `CMAKE_OBJECT_PATH_MAX=250`. Final log: `D:/CodexBuilds/operational-android-20261005/build-fresh-native.log`: **BUILD SUCCESSFUL in 4m 27s**, exit 0, 466 actionable tasks.

The initial build failed because the host Java locale caused Room/KSP to emit Arabic numeric literals in generated Kotlin. English build-tool locale regenerated valid source, verified with zero Arabic digits, and compilation passed. Later Ninja failures were Windows 260-character path limits. Short cache/native staging paths resolved them without application source changes. A generated-output deletion command was rejected by automatic approval review with `blocked by policy`; no deletion was performed or retried. Fresh staging preserves the old generated outputs. No security policy was changed, and ADB was not invoked in this phase.

These are ordinary build configuration mechanisms documented by [CMake](https://cmake.org/cmake/help/latest/variable/CMAKE_OBJECT_PATH_MAX.html) and [Android Gradle native build configuration](https://developer.android.com/reference/tools/gradle-api/8.11/com/android/build/api/dsl/Cmake).

Machine-readable provenance and retained command output are in `evidence/apk-provenance.json`, `apk-badging.txt`, `apk-manifest.txt`, and `apk-signature.txt`. The artifact directory also contains `APK_PROVENANCE.json`. The APK is retained locally and is not committed to Git.

## Stop condition

No installation, launch, receipt upload, or device scenario is claimed. User accepted the infrastructure blocker and instructed us to stop after APK preparation until an approved Android channel exists. Desktop/static evidence does not replace Device UAT. The prior application 15/15, backend 140/140, and frontend 38/38 results remain unchanged; they were not reopened in this APK-only phase.

No Merge, Prepare, Prepublish, Deploy, Accounting writer, Legacy usage, or MZ2 Accounting change. Production data unchanged by this task. Production financial writes = 0.
