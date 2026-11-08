"""Install current official Linux x64 toolchains with verified download receipts."""

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import tomllib
from pathlib import Path


def resolve_npm(tag: str = "latest") -> dict[str, str]:
    package = json.loads(read(f"https://registry.npmjs.org/npm/{tag}"))
    version = package["version"]
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("npm must be a stable release")
    if tag == "next-11" and not version.startswith("11."):
        raise ValueError("Node 20 requires the compatible npm 11 release line")
    integrity = package["dist"]["integrity"]
    if not integrity.startswith("sha512-"):
        raise ValueError("npm archive requires official SHA-512 integrity")
    checksum = base64.b64decode(integrity.removeprefix("sha512-"), validate=True)
    if len(checksum) != 64:
        raise ValueError("invalid npm SHA-512 integrity")
    return {
        "version": version,
        "url": package["dist"]["tarball"],
        "sha512": checksum.hex(),
    }


def download(url: str, destination: Path) -> None:
    subprocess.run(
        [
            "curl",
            "-fsSL",
            "--retry",
            "5",
            "--retry-all-errors",
            "--retry-delay",
            "2",
            url,
            "-o",
            str(destination),
        ],
        check=True,
    )


def read(url: str) -> bytes:
    # Hosted CI reuses its normal GitHub CLI token; no token is passed to guests.
    if url.startswith("https://api.github.com/") and os.environ.get("GH_TOKEN"):
        return subprocess.run(
            ["gh", "api", url], check=True, capture_output=True
        ).stdout
    # A real file lets curl discard a partial response before retrying.
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "response"
        download(url, path)
        return path.read_bytes()


def resolve() -> dict[str, dict[str, str]]:
    node = next(
        item
        for item in json.loads(read("https://nodejs.org/dist/index.json"))
        if item["lts"] and re.fullmatch(r"v\d+\.\d+\.\d+", item["version"])
    )
    filename = f"node-{node['version']}-linux-x64.tar.xz"
    node_root = f"https://nodejs.org/dist/{node['version']}/"
    checksum = next(
        line.split()[0]
        for line in read(node_root + "SHASUMS256.txt").decode().splitlines()
        if line.split()[-1] == filename
    )
    go = next(
        item
        for item in json.loads(read("https://go.dev/dl/?mode=json"))
        if item["stable"]
    )
    go_file = next(
        item
        for item in go["files"]
        if item["os"] == "linux"
        and item["arch"] == "amd64"
        and item["kind"] == "archive"
    )
    rust = tomllib.loads(
        read("https://static.rust-lang.org/dist/channel-rust-stable.toml").decode()
    )
    release = json.loads(
        read("https://api.github.com/repos/git-lfs/git-lfs/releases/latest")
    )
    asset = next(
        item
        for item in release["assets"]
        if item["name"].startswith("git-lfs-linux-amd64-")
        and item["name"].endswith(".tar.gz")
    )
    result = {
        "node": {
            "version": node["version"],
            "url": node_root + filename,
            "sha256": checksum,
        },
        "go": {
            "version": go["version"],
            "url": "https://go.dev/dl/" + go_file["filename"],
            "sha256": go_file["sha256"],
        },
        "git-lfs": {
            "version": release["tag_name"],
            "url": asset["browser_download_url"],
            "sha256": asset["digest"].removeprefix("sha256:"),
        },
        "npm": resolve_npm(),
    }
    for component in ("rustc", "rust-std", "cargo"):
        package = rust["pkg"][component]
        target = package["target"]["x86_64-unknown-linux-gnu"]
        result[component] = {
            "version": package["version"].split()[0],
            "url": target["xz_url"],
            "sha256": target["xz_hash"],
        }
    return result


def unpack(spec: dict[str, str], directory: Path) -> Path:
    algorithm = "sha512" if "sha512" in spec else "sha256"
    if not re.fullmatch(
        r"[a-f0-9]{128}" if algorithm == "sha512" else r"[a-f0-9]{64}",
        spec[algorithm],
    ):
        raise ValueError("missing or invalid official archive checksum")
    archive = directory / "download.tar"
    download(spec["url"], archive)
    with archive.open("rb") as stream:
        actual = hashlib.file_digest(stream, algorithm).hexdigest()
    if actual != spec[algorithm]:
        raise ValueError("official toolchain archive checksum mismatch")
    extracted = directory / "extracted"
    extracted.mkdir()
    with tarfile.open(archive) as stream:
        stream.extractall(extracted, filter="data")
    return extracted


def install_npm(prefix: Path, spec: dict[str, str], extracted: Path) -> None:
    package = extracted / "package"
    if json.loads((package / "package.json").read_text())["version"] != spec["version"]:
        raise ValueError("npm archive version differs from registry receipt")
    destination = prefix / "lib/node_modules/npm"
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(package, destination, symlinks=True)
    (prefix / "bin").mkdir(parents=True, exist_ok=True)
    for command in ("npm", "npx"):
        link = prefix / "bin" / command
        link.unlink(missing_ok=True)
        link.symlink_to(f"../lib/node_modules/npm/bin/{command}-cli.js")


def refresh_runner_npm(runner: Path, receipt: Path) -> None:
    # Preserve the runner's actual Node binaries and its action routing contract.
    # npm 12 supports Node 24; npm 11 is the maintained compatible line for Node 20.
    specifications = json.loads(receipt.read_text())
    for runtime, tag in (("node20", "next-11"), ("node24", "latest")):
        prefix = runner / "externals" / runtime
        if not (prefix / "bin/node").is_file():
            raise ValueError(f"missing official runner runtime: {runtime}")
        spec = resolve_npm(tag)
        with tempfile.TemporaryDirectory() as directory:
            extracted = unpack(spec, Path(directory))
            install_npm(prefix, spec, extracted)
        specifications[f"npm-{runtime}"] = spec
        print(f"Installed runner {runtime} npm {spec['version']}", flush=True)
    receipt.write_text(json.dumps(specifications, indent=2) + "\n")


def install(prefix: Path, receipt: Path) -> None:
    specifications = resolve()
    prefix.mkdir(parents=True, exist_ok=True)
    (prefix / "bin").mkdir(exist_ok=True)
    for name, spec in specifications.items():
        with tempfile.TemporaryDirectory() as directory:
            extracted = unpack(spec, Path(directory))
            if name == "node":
                shutil.copytree(
                    next(extracted.glob("node-*")),
                    prefix,
                    dirs_exist_ok=True,
                    symlinks=True,
                )
            elif name == "go":
                if (prefix / "go").exists():
                    raise ValueError("refusing to merge an existing Go installation")
                shutil.copytree(extracted / "go", prefix / "go", symlinks=True)
            elif name == "git-lfs":
                candidates = [p for p in extracted.rglob("git-lfs") if p.is_file()]
                if len(candidates) != 1:
                    raise ValueError("Git LFS archive must contain one executable")
                shutil.copy2(candidates[0], prefix / "bin/git-lfs")
            elif name == "npm":
                install_npm(prefix, spec, extracted)
            else:
                installer = next(extracted.glob("*/install.sh"))
                subprocess.run(
                    [
                        "bash",
                        str(installer),
                        f"--prefix={prefix}",
                        "--disable-ldconfig",
                    ],
                    check=True,
                )
        print(f"Installed {name} {spec['version']}", flush=True)
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(json.dumps(specifications, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prefix", type=Path)
    parser.add_argument("receipt", type=Path)
    parser.add_argument("--runner-npm", action="store_true")
    args = parser.parse_args()
    if args.runner_npm:
        refresh_runner_npm(args.prefix.resolve(), args.receipt.resolve())
    else:
        install(args.prefix.resolve(), args.receipt.resolve())
