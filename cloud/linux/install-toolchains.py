"""Install current official Linux x64 toolchains with verified download receipts."""

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import tarfile
import tempfile
import tomllib
from pathlib import Path


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
    if not re.fullmatch(r"[a-f0-9]{64}", spec["sha256"]):
        raise ValueError("missing or invalid official archive checksum")
    archive = directory / "download.tar"
    download(spec["url"], archive)
    with archive.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != spec["sha256"]:
        raise ValueError("official toolchain archive checksum mismatch")
    extracted = directory / "extracted"
    extracted.mkdir()
    with tarfile.open(archive) as stream:
        stream.extractall(extracted, filter="data")
    return extracted


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
    args = parser.parse_args()
    install(args.prefix.resolve(), args.receipt.resolve())
