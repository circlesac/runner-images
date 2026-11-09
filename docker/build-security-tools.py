"""Rebuild maintained container tools with patched, checksum-verified Go modules."""

import json
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

from security_inputs import fetch


def build(inputs, destination):
    import hashlib

    destination.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "CGO_ENABLED": "0", "GOOS": "linux", "GOARCH": "arm64"}
    for name, source in inputs["sources"].items():
        work = Path("/tmp/security-build") / name
        work.mkdir(parents=True)
        archive = work / "source.tar.gz"
        data = fetch(source["url"])
        if hashlib.sha256(data).hexdigest() != source["sha256"]:
            raise ValueError("source checksum mismatch: " + name)
        archive.write_bytes(data)
        with tarfile.open(archive) as stream:
            stream.extractall(work, filter="data")
        root = next(path for path in work.iterdir() if path.is_dir())
        if name == "buildx":
            # Buildx only uses this frozen name generator from the legacy daemon
            # module. Keep the exact licensed utility, without that daemon dependency.
            source_package = root / "vendor/github.com/docker/docker"
            local_package = root / "internal/namesgenerator"
            local_package.mkdir(parents=True)
            for filename in ["LICENSE", "NOTICE", "AUTHORS"]:
                shutil.copy2(source_package / filename, local_package / filename)
            shutil.copy2(
                source_package / "pkg/namesgenerator/names-generator.go",
                local_package / "names-generator.go",
            )
            caller = root / "store/util.go"
            old = '"github.com/docker/docker/pkg/namesgenerator"'
            text = caller.read_text()
            if text.count(old) != 1:
                raise ValueError("Buildx legacy name-generator import changed")
            caller.write_text(
                text.replace(old, '"github.com/docker/buildx/internal/namesgenerator"')
            )
            subprocess.run(["go", "mod", "tidy"], cwd=root, env=env, check=True)
        manifest = (root / "go.mod").read_text()
        module = next(
            line.removeprefix("module ").strip()
            for line in manifest.splitlines()
            if line.startswith("module ")
        )
        updates = [
            path + "@" + version
            for path, version in inputs["go_modules"].items()
            if path + " " in manifest
        ]
        subprocess.run(["go", "get", *updates], cwd=root, env=env, check=True)
        targets = {
            "buildx": [("./cmd/buildx", "docker-buildx")],
            "compose": [("./cmd", "docker-compose")],
            "containerd": [("./cmd/containerd", "containerd"), ("./cmd/ctr", "ctr")],
        }
        flags = (
            f"-X {module}/version.Version={source['version']}-security "
            f"-X {module}/internal.Version={source['version']}-security"
        )
        for package, output in targets[name]:
            subprocess.run(
                [
                    "go",
                    "build",
                    "-mod=mod",
                    "-tags",
                    "no_btrfs,netgo,osusergo",
                    "-ldflags",
                    flags,
                    "-o",
                    str(destination / output),
                    package,
                ],
                cwd=root,
                env=env,
                check=True,
            )
            with (destination / (output + ".buildinfo.txt")).open("w") as evidence:
                subprocess.run(
                    ["go", "version", "-m", str(destination / output)],
                    check=True,
                    stdout=evidence,
                )
        if name == "buildx":
            dependencies = subprocess.check_output(
                ["go", "list", "-mod=mod", "-deps", "./cmd/buildx"],
                cwd=root,
                env=env,
                text=True,
            )
            if any(
                line.startswith("github.com/docker/docker/")
                for line in dependencies.splitlines()
            ):
                raise ValueError("Buildx still links the legacy Docker module")
            (destination / "buildx.packages.txt").write_text(dependencies)
            shutil.copytree(local_package, destination / "buildx-namesgenerator")
        (destination / (name + ".go.mod")).write_bytes((root / "go.mod").read_bytes())
        (destination / (name + ".go.sum")).write_bytes((root / "go.sum").read_bytes())


if __name__ == "__main__":
    build(json.loads(sys.argv[1]), Path(sys.argv[2]))
