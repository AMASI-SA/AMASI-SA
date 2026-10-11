$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath 'C:/Users/amasi/.codex/tmp/operational-android-review-20261005-ui'
$env:JAVA_HOME='C:/Program Files/Android/Android Studio/jbr'
$env:ANDROID_HOME='C:/Users/amasi/AppData/Local/Android/Sdk'
$env:ANDROID_SDK_ROOT=$env:ANDROID_HOME
$env:PATH='C:/Users/amasi/.codex/tmp/track-h-node/node-v22.23.2-win-x64;'+$env:JAVA_HOME+'/bin;'+$env:PATH
$env:CI='1'
$env:EXPO_NO_TELEMETRY='1'
$env:EXPO_PUBLIC_MEZAN_API_MODE='real'
$env:EXPO_PUBLIC_MEZAN_API_BASE_URL='http://10.0.2.2:8135/api'
$env:EXPO_PUBLIC_MEZAN_WRITE_ENABLED='false'
$env:EXPO_PUBLIC_OPERATIONAL_MOVEMENTS_WRITE_ENABLED='true'
& 'C:/Users/amasi/AppData/Local/Android/Sdk/cmdline-tools/latest/bin/sdkmanager.bat' 'platforms;android-36'
if($LASTEXITCODE -ne 0){exit $LASTEXITCODE}
& 'C:/Users/amasi/.codex/tmp/track-h-node/node-v22.23.2-win-x64/node.exe' node_modules/expo/bin/cli prebuild --platform android --no-install
if($LASTEXITCODE -ne 0){exit $LASTEXITCODE}
& 'C:/Users/amasi/.codex/tmp/mz2-track-f-python/Scripts/python.exe' configure-generated-android.py
if($LASTEXITCODE -ne 0){exit $LASTEXITCODE}
Set-Location -LiteralPath 'C:/Users/amasi/.codex/tmp/operational-android-review-20261005-ui/android'
& ./gradlew.bat assembleDebug --no-daemon
exit $LASTEXITCODE
