"""Replace vulnerable bundled tools; preserve their invocation paths and ABIs."""

import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

from security_inputs import fetch


def download(item, destination):
    cache = Path("/opt/runner-security-assets") / item["sha256"]
    data = cache.read_bytes() if cache.is_file() else fetch(item["url"])
    if hashlib.sha256(data).hexdigest() != item["sha256"]:
        raise ValueError("replacement checksum mismatch")
    destination.write_bytes(data)
    return destination


def patch_jar(target, replacements, versions=None):
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
    try:
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
                            or entry.filename.startswith(
                                f"META-INF/maven/{group}/{name}/"
                            )
                        ):
                            continue
                        if entry.filename in names:
                            continue
                        output.writestr(entry, dependency.read(entry))
                        names.add(entry.filename)
                metadata = f"META-INF/maven/{group}/{name}/pom.properties"
                if metadata not in names:
                    if versions is None or name not in versions:
                        raise ValueError("replacement version evidence is missing")
                    output.writestr(
                        metadata,
                        f"groupId={group}\nartifactId={name}\nversion={versions[name]}\n",
                    )
                    names.add(metadata)
        staged.replace(target)
    finally:
        staged.unlink(missing_ok=True)


def upgrade_sdk_jar(android, name, replacement, version):
    matches = list(android.glob(f"external/**/{name}-*.jar"))
    if len(matches) != 1:
        raise ValueError("expected one SDK dependency: " + name)
    old = matches[0]
    new = old.parent.parent / version / f"{name}-{version}.jar"
    new.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(replacement, new)
    if old == new:
        return
    # Signed upstream dependencies stay byte-for-byte intact. Only unsigned SDK
    # classpath manifests change to reference the real replacement version.
    for classpath in android.glob("*-classpath.jar"):
        staged = classpath.with_suffix(".patched.jar")
        try:
            with (
                zipfile.ZipFile(classpath) as source,
                zipfile.ZipFile(staged, "w") as output,
            ):
                for entry in source.infolist():
                    if entry.filename.upper().endswith((".SF", ".RSA", ".DSA")):
                        raise ValueError("refusing to edit a signed classpath archive")
                    data = source.read(entry)
                    if entry.filename == "META-INF/MANIFEST.MF":
                        data = data.replace(
                            str(old.relative_to(android)).encode(),
                            str(new.relative_to(android)).encode(),
                        )
                    output.writestr(entry, data)
            staged.replace(classpath)
        finally:
            staged.unlink(missing_ok=True)
    old.unlink()


def file_version(data):
    """Read VS_FIXEDFILEINFO.dwFileVersion from a PE image."""
    offset = data.find(b"\xbd\x04\xef\xfe")
    if offset < 0:
        raise ValueError("assembly has no fixed file version")
    _, _, high, low = struct.unpack_from("<4I", data, offset)
    return f"{high >> 16}.{high & 0xFFFF}.{low >> 16}.{low & 0xFFFF}"


def upgrade_runner_assemblies(bin_dir, packages, work):
    """Replace app-local NuGet assemblies and keep the runner's deps.json exact."""
    manifest = bin_dir / "Runner.Worker.deps.json"
    deps = json.loads(manifest.read_text())
    old_versions = {}
    for name in packages:
        keys = [k for k in deps["libraries"] if k.startswith(name + "/")]
        if len(keys) != 1:
            raise ValueError("runner does not reference exactly one " + name)
        old_versions[name] = keys[0].split("/", 1)[1]
    for name, item in packages.items():
        old, new = old_versions[name], item["version"]
        archive = download(item, work / (name + ".nupkg"))
        member = f"lib/net8.0/{name}.dll"
        with zipfile.ZipFile(archive) as package:
            data = package.read(member)
        (bin_dir / (name + ".dll")).write_bytes(data)
        for target in deps["targets"].values():
            entry = target.pop(f"{name}/{old}", None)
            if entry is None:
                continue
            entry["runtime"][member]["fileVersion"] = file_version(data)
            target[f"{name}/{new}"] = entry
            for other in target.values():
                dependencies = other.get("dependencies", {})
                if dependencies.get(name) == old:
                    dependencies[name] = new
        library = deps["libraries"].pop(f"{name}/{old}")
        library.update(
            sha512="sha512-" + item["sha512"],
            path=f"{name.lower()}/{new}",
            hashPath=f"{name.lower()}.{new}.nupkg.sha512",
        )
        deps["libraries"][f"{name}/{new}"] = library
    manifest.write_text(json.dumps(deps, indent=2) + "\n")


def harden(inputs):
    with tempfile.TemporaryDirectory(prefix="runner-security-") as temporary:
        work = Path(temporary)
        runner = Path("/opt/actions-runner")
        for slot, key in (("node20", "node_compat"), ("node24", "node24")):
            node_archive = download(inputs[key], work / (slot + ".tar.xz"))
            runtime = runner / "externals" / slot
            with tarfile.open(node_archive) as stream:
                stream.extractall(work / slot, filter="data")
            source = next((work / slot).iterdir())
            # OverlayFS cannot rename a populated directory from a lower image
            # layer. The verified replacement is extracted before discarding
            # the old runtime.
            if not (source / "bin/node").is_file():
                raise ValueError("replacement Node runtime is incomplete")
            shutil.rmtree(runtime)
            shutil.copytree(source, runtime)
        upgrade_runner_assemblies(runner / "bin", inputs["nuget"], work)
        # The AMD64 runner also ships musl runtimes for Alpine job containers.
        # They cannot run or be patched on this glibc image, and the ARM64
        # runner never ships them, so both architectures keep the same contract.
        for musl in sorted((runner / "externals").glob("node*_alpine")):
            shutil.rmtree(musl)
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
        versions = {name: item["version"] for name, item in inputs["jars"].items()}
        patch_jar(
            compiler,
            {
                name: jars[name]
                for name in ["protobuf-java", "jline-builtins", "jline-remote-telnet"]
            },
            versions,
        )
        for name in ["bcprov-jdk18on", "guava"]:
            upgrade_sdk_jar(android, name, jars[name], versions[name])
        # sdkmanager now delegates to the Android CLI, which downloads a bundle
        # (main.jar plus its own JRE) into the invoking user's ~/.android/cli.
        # The build ran it as root; jobs run as runner and fetch their own, so
        # root's copy is an unused, unpatchable build cache.
        shutil.rmtree(Path("/root/.android/cli"), ignore_errors=True)
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
        subprocess.run(
            ["chown", "-R", "runner:docker", str(runner), str(python_root)], check=True
        )
        shutil.rmtree(Path("/root/.npm"), ignore_errors=True)
        Path("/opt/runner-security-inputs.json").write_text(
            json.dumps(inputs, indent=2)
        )


if __name__ == "__main__":
    harden(json.loads(sys.argv[1]))
