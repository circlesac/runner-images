#!/usr/bin/env bash
set -euo pipefail
# shellcheck disable=SC1091
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
# Exercise patched embedded runtimes and the non-root runner staging contract.
for runtime in /opt/actions-runner/externals/node*/bin/node; do
	"$runtime" --version
	"$runtime" "${runtime%/bin/node}/lib/node_modules/npm/bin/npm-cli.js" --version
done
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
mkdir "$work/runner"
cp -a /opt/actions-runner/. "$work/runner/"
ndk="$ANDROID_HOME/ndk/27.1.12297006/toolchains/llvm/prebuilt/linux-x86_64"
"$ndk/python3/bin/python3.11" -c 'import ssl, sqlite3, ctypes; print(ssl.OPENSSL_VERSION)'
# The NDK-supported wrapper supplies Python home and its dynamic library path.
"$ndk/bin/lldb.sh" -b -o 'script import ssl, ctypes; print("lldb-python-passed")' -o quit >"$work/lldb.log" 2>&1
cat "$work/lldb.log"
grep -qx 'lldb-python-passed' "$work/lldb.log"
if grep -q Traceback "$work/lldb.log"; then exit 1; fi
printf 'int main(void) { return 0; }\n' >"$work/native.c"
"$ndk/bin/aarch64-linux-android26-clang" "$work/native.c" -o "$work/native"
# This is the SDK's reduced Kotlin/PSI compiler, exercised through Android lint.
mkdir -p "$work/project/src" "$work/project/bin/classes"
printf '<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="com.example.smoke"><uses-sdk android:minSdkVersion="26" android:targetSdkVersion="36"/><application/></manifest>\n' >"$work/project/AndroidManifest.xml"
printf 'public class Smoke { public int value() { return 1; } }\n' >"$work/project/src/Smoke.java"
javac -d "$work/project/bin/classes" "$work/project/src/Smoke.java"
printf 'fun smoke() = android.app.NotificationChannel("id", "name", 3)\n' >"$work/project/src/Main.kt"
lint --quiet --exitcode --check LintError,NewApi --text "$work/lint.txt" "$work/project"
cat "$work/lint.txt"
"$ANDROID_HOME/build-tools/36.0.0/aapt2" link -o "$work/smoke.apk" -I "$ANDROID_HOME/platforms/android-36/android.jar" --manifest "$work/project/AndroidManifest.xml"
docker buildx version
docker compose version
containerd --version
test -x /opt/actions-runner/bin/Runner.Listener
printf 'native-arm64-android-toolchain-passed\n'
