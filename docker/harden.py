"""Replace vulnerable bundled tools; preserve their invocation paths and ABIs."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

from security_inputs import fetch


def download(item, destination):
    data = fetch(item["url"])
    if hashlib.sha256(data).hexdigest() != item["sha256"]:
        raise ValueError("replacement checksum mismatch")
    destination.write_bytes(data)
    return destination


def patch_jar(target, replacements):
    """Replace complete embedded dependency namespaces and their version metadata."""
    package_roots = {
        "protobuf-java": ("com/google/protobuf/", "com.google.protobuf"),
        "jline-builtins": ("org/jline/builtins/", "org.jline"),
        "jline-remote-telnet": ("org/jline/builtins/telnet/", "org.jline"),
        "guava": ("com/google/common/", "com.google.guava"),
    }
    removed = [
        root
        for name in replacements
        for root in [
            package_roots[name][0],
            f"META-INF/maven/{package_roots[name][1]}/{name}/",
        ]
    ]
    staged = target.with_suffix(".patched.jar")
    with (
        zipfile.ZipFile(target) as old,
        zipfile.ZipFile(staged, "w", zipfile.ZIP_DEFLATED) as output,
    ):
        names = set()
        for entry in old.infolist():
            if any(entry.filename.startswith(prefix) for prefix in removed):
                continue
            if entry.filename.upper().startswith(
                "META-INF/"
            ) and entry.filename.upper().endswith((".SF", ".RSA", ".DSA")):
                raise ValueError("refusing to edit a signed shaded archive")
            output.writestr(entry, old.read(entry))
            names.add(entry.filename)
        for name, path in replacements.items():
            root, group = package_roots[name]
            with zipfile.ZipFile(path) as dependency:
                for entry in dependency.infolist():
                    if not (
                        entry.filename.startswith(root)
                        or entry.filename.startswith(f"META-INF/maven/{group}/{name}/")
                    ):
                        continue
                    if entry.filename in names:
                        continue
                    output.writestr(entry, dependency.read(entry))
                    names.add(entry.filename)
    staged.replace(target)


def harden(inputs):
    with tempfile.TemporaryDirectory(prefix="runner-security-") as temporary:
        work = Path(temporary)
        runner = Path("/opt/actions-runner")
        node_archive = download(inputs["node_compat"], work / "node.tar.xz")
        runtime = runner / "externals/node20"
        old = runtime.with_name("node20.original")
        runtime.rename(old)
        runtime.mkdir()
        with tarfile.open(node_archive) as stream:
            stream.extractall(work / "node", filter="data")
        source = next((work / "node").iterdir())
        shutil.copytree(source, runtime, dirs_exist_ok=True)
        shutil.rmtree(old)
        for runtime in sorted((runner / "externals").glob("node*")):
            if not (runtime / "bin/node").is_file():
                continue
            subprocess.run(
                [
                    str(runtime / "bin/node"),
                    "/usr/local/lib/node_modules/npm/bin/npm-cli.js",
                    "install",
                    "--global",
                    "--prefix",
                    str(runtime),
                    "npm@" + inputs["npm_version"],
                    "--no-audit",
                    "--no-fund",
                ],
                check=True,
            )
        python_root = Path(
            "/opt/android-sdk/ndk/27.1.12297006/toolchains/llvm/prebuilt/linux-x86_64/python3"
        )
        archive = download(inputs["python"], work / "python.tar.gz")
        with tarfile.open(archive) as stream:
            stream.extractall(work / "python", filter="data")
        shutil.rmtree(python_root)
        shutil.copytree(work / "python/python", python_root)
        subprocess.run(
            [str(python_root / "bin/python3.11"), "-m", "ensurepip", "--upgrade"],
            check=True,
        )
        subprocess.run(
            [
                str(python_root / "bin/python3.11"),
                "-m",
                "pip",
                "install",
                "--upgrade",
                "pip",
                "setuptools",
                "wheel",
            ],
            check=True,
        )
        jars = {
            name: download(item, work / (name + ".jar"))
            for name, item in inputs["jars"].items()
        }
        android = Path("/opt/android-sdk/cmdline-tools/latest/lib")
        compiler = android / "external/lint-psi/kotlin-compiler/kotlin-compiler-mvn.jar"
        patch_jar(
            compiler,
            {
                name: jars[name]
                for name in ["protobuf-java", "jline-builtins", "jline-remote-telnet"]
            },
        )
        for name in ["bcprov-jdk18on", "guava"]:
            matches = list(android.glob(f"external/**/{name}-*.jar"))
            if len(matches) != 1:
                raise ValueError("expected one SDK dependency: " + name)
            shutil.copy2(jars[name], matches[0])
        for cache in Path("/root/.android/cli/bundles").glob("*/main.jar"):
            patch_jar(cache, {"guava": jars["guava"]})
        tools = Path("/opt/runner-security-tools")
        for output, paths in {
            "docker-buildx": [
                "/usr/libexec/docker/cli-plugins/docker-buildx",
                "/usr/local/lib/docker/cli-plugins/docker-buildx",
            ],
            "docker-compose": ["/usr/libexec/docker/cli-plugins/docker-compose"],
            "containerd": ["/usr/bin/containerd"],
            "ctr": ["/usr/bin/ctr"],
        }.items():
            for path in paths:
                shutil.copy2(tools / output, path)
                os.chmod(path, 0o755)
        shutil.rmtree(Path("/root/.npm"), ignore_errors=True)
        Path("/opt/runner-security-inputs.json").write_text(
            json.dumps(inputs, indent=2)
        )


if __name__ == "__main__":
    harden(json.loads(sys.argv[1]))
