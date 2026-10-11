# OPERATIONAL_BALANCE_FINAL_REVIEW_BLOCKED

Current APK-only milestone: [APK_READY.md](APK_READY.md). A verified isolated APK is now available; the earlier running-build narrative below is historical. Device UAT remains blocked and was not attempted.

The application blockers are resolved:15/15 application verifiers and TypeScript PASS. Fresh financial regression remains140 Backend and38 frontend PASS. No financial implementation was changed in this phase. Detailed diagnosis: APP_BLOCKERS.md. Existing MZ2-only, Legacy rejection, idempotency and parity tests remain green.

## Remaining blocker

Device UAT was not executed. Automatic approval review rejected the Android ADB UI launch/hierarchy command with `blocked by policy`; no more specific reason was returned. The available CUA inventory contains no native apps or Android device, only the in-app desktop browser. No alternate route was used to evade that rejection, and the production APK was not opened.

## Approved build preparation

The original Android CLI bundled-JRE rejection was avoided through Android's documented Gradle SDK auto-download using existing licenses and a separately installed signed Microsoft OpenJDK21. Application Control was not modified. Full C: storage interrupted extraction; only the failed task-created Gradle archive/extraction was moved to the dedicated D: task directory. SDK, Gradle caches, dependencies and temp now live there. The isolated package is com.amasi.sa.operationalpreview, OTA disabled, local10.0.2.2:8135API, other financial writers disabled. All120tracked app/src/assets files match native641537f exactly.

The bounded-network build is still running under exec session42944, log D:/CodexBuilds/operational-android-20261005/build-final.log. It has reached Android Gradle plugin dependency downloads. No APK success or device pass is claimed. Next safe build action is reading that session/log; no duplicate build should be started while it runs. Once an APK exists, inspect its package/embedded API/OTA settings, then use an environment-approved Android testing interface for all16requested scenarios. Browser tests do not replace Device UAT.

Official setup references:
- https://developer.android.com/studio/intro/update.html (Gradle SDK auto-download with accepted licenses)
- https://learn.microsoft.com/en-us/java/openjdk/download (Microsoft JDK21 distribution)

No merge, Prepare, Prepublish, deploy, Accounting writer, Legacy fallback or MZ2 Accounting change. Production data unchanged by this task; production financial writes=0.
