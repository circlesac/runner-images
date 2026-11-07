import json
import os
import subprocess
from pathlib import Path

import pytest


def test_build_lifecycle_and_failure_cleanup(tmp_path):
    root = Path(__file__).resolve().parents[1]
    tools = tmp_path / "bin"
    tools.mkdir()
    state = tmp_path / "state.json"
    state.write_text(json.dumps(["base", "unrelated-image"]))
    tart = tools / "tart"
    tart.write_text("""#!/usr/bin/env python3
import sys, os, json, time
from pathlib import Path
a=sys.argv[1:]
state=Path(os.environ["TEST_TART_STATE"])
images=json.loads(state.read_text())
command=a[0]
if command=="list":
    if "--format" in a:
        status="running" if os.environ.get("TEST_FAIL_STAGE")=="capacity" else "stopped"
        print(json.dumps([{"State":status} for _ in images]))
    else: print("\\n".join(images))
    sys.exit(0)
if command=="clone": images.append(a[2])
if command=="delete": images.remove(a[1])
if command=="rename":
    if os.environ.get("TEST_FAIL_STAGE")=="rename": sys.exit(4)
    images.remove(a[1]); images.append(a[2])
if command=="stop": (state.parent/(a[1]+".stopped")).touch()
if command=="run":
    if os.environ.get("TEST_FAIL_STAGE")=="boot": sys.exit(7)
    while not (state.parent/(a[1]+".stopped")).exists(): time.sleep(.01)
    sys.exit(0)
if command=="exec":
    if os.environ.get("TEST_FAIL_STAGE")=="boot" and "/usr/bin/true" in a: sys.exit(1)
    if "cmdline-tools" in " ".join(a): print("21 12266719 35 35.0.0")
    body=sys.stdin.read() if "-i" in a else ""
    if "brew update" in body:
        if os.environ.get("TEST_FAIL_STAGE")=="provision": sys.exit(5)
        print("/example/bin:/usr/bin:/bin")
    if "Missing hosted tool cache entry" in body:
        if os.environ.get("TEST_FAIL_STAGE")=="verify": sys.exit(6)
    if "json.dumps(collect()" in body:
        print(json.dumps({"schema_version":1,"os":"darwin","arch":"arm64",
            "image_os":"macos26","image_version":"example-build",
            "records":["cmd bash"]}))
state.write_text(json.dumps(images))
""")
    commands = {
        "uname": 'if [ "$1" = -s ]; then echo Darwin; else echo arm64; fi',
        "networksetup": "echo 'Device: en0'",
        "route": "echo 'interface: en0'",
        "ifconfig": "echo 'status: active'",
        "ipconfig": "echo '192.0.2.10'",
        "curl": 'echo \'{"tag_name":"v2.338.2","assets":[{'
        '"name":"actions-runner-osx-arm64-2.338.2.tar.gz","digest":"sha256:'
        + "a" * 64
        + "\"}]}'",
    }
    for name, script in commands.items():
        (tools / name).write_text("#!/bin/sh\n" + script + "\n")
    for path in tools.iterdir():
        path.chmod(0o755)
    for failure in ("capacity", "boot", "provision", "verify", "rename", "", "android"):
        image = f"test-image-{tmp_path.name}-{failure or 'success'}"
        env = {
            **os.environ,
            "PATH": str(tools) + os.pathsep + os.environ["PATH"],
            "TEST_TART_STATE": str(state),
            "TEST_FAIL_STAGE": "" if failure == "android" else failure,
        }
        result = subprocess.run(
            [
                "bash",
                str(root / "macos/build-image.sh"),
                image,
                "base",
                "xcode-android" if failure == "android" else "xcode",
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        images = json.loads(state.read_text())
        assert "base" in images and "unrelated-image" in images
        assert not any("-build-" in name for name in images)
        receipt = root / "artifacts/tart" / f"{image}.json"
        if failure and failure != "android":
            assert result.returncode != 0, result.stdout + result.stderr
            assert image not in images
        else:
            assert result.returncode == 0, result.stdout + result.stderr
            assert image in images
            assert json.loads(receipt.read_text())["records"] == ["cmd bash"]
            repeated = subprocess.run(
                ["bash", str(root / "macos/build-image.sh"), image, "base"],
                env=env,
                capture_output=True,
            )
            assert repeated.returncode != 0
            assert json.loads(state.read_text()) == images
        receipt.unlink(missing_ok=True)


@pytest.mark.parametrize(
    "arguments",
    [
        ["../unsafe"],
        ["base", "base"],
        ["example", "base", "unknown-profile"],
        ["example", "base", "xcode", "/"],
    ],
)
def test_invalid_build_inputs(arguments):
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ["bash", str(root / "macos/build-image.sh"), *arguments], capture_output=True
    )
    assert result.returncode != 0


def test_runner_installer_rejects_unsafe_directory(tmp_path):
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ["bash", str(root / "macos/image/install-runner.sh"), "2.338.2", "/", "a" * 64],
        capture_output=True,
    )
    assert result.returncode != 0
    assert b"below the guest home" in result.stderr
