"""Rebuild maintained container tools with patched, checksum-verified Go modules."""

import json
import os
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
        (destination / (name + ".go.mod")).write_bytes((root / "go.mod").read_bytes())
        (destination / (name + ".go.sum")).write_bytes((root / "go.sum").read_bytes())


if __name__ == "__main__":
    build(json.loads(sys.argv[1]), Path(sys.argv[2]))
