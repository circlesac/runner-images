#!/usr/bin/env bash
set -euo pipefail
source /etc/os-release
test "$VERSION_ID" = 24.04
test "$(uname -m)" = aarch64
test "$(id -un)" = runner
for tool in git jq python3 node npm java docker sdkmanager make g++; do command -v "$tool"; done
node --version
npm --version
java -version
sdkmanager --version
"$ANDROID_HOME/build-tools/36.0.0/aapt2" version
test -x "$ANDROID_HOME/ndk/27.1.12297006/toolchains/llvm/prebuilt/linux-x86_64/bin/clang"
test -x /opt/actions-runner/bin/Runner.Listener
test ! -e /opt/actions-runner/.credentials
test ! -e /opt/actions-runner/.runner
printf 'native-arm64-android-toolchain-passed\n'
