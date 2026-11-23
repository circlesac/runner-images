"""Resolve checksum-bound security replacements for bundled build tools."""

import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import unquote, urlsplit
from urllib.request import Request, urlopen


def fetch(url):
    for attempt in range(5):
        try:
            headers = {"User-Agent": "runner-images-builder/1.0"}
            if url.startswith("https://api.github.com/") and os.environ.get("GH_TOKEN"):
                headers["Authorization"] = "Bearer " + os.environ["GH_TOKEN"]
            with urlopen(
                Request(url, headers=headers),
                timeout=120,
            ) as response:
                return response.read()
        except OSError:
            if attempt == 4:
                raise
            time.sleep(attempt + 1)
    raise RuntimeError("download did not complete")


def github(repo, path):
    return json.loads(fetch(f"https://api.github.com/repos/{repo}/{path}"))


def asset(value):
    digest = value.get("digest", "")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError("GitHub asset is missing its SHA256 digest")
    return {
        "url": value["browser_download_url"],
        "sha256": digest.removeprefix("sha256:"),
    }


def maven(group, artifact, major):
    root = f"https://repo.maven.apache.org/maven2/{group.replace('.', '/')}/{artifact}"
    versions = ET.fromstring(fetch(root + "/maven-metadata.xml")).findall(
        "versioning/versions/version"
    )
    values = [
        v.text
        for v in versions
        if v.text
        and re.fullmatch(r"\d+(?:\.\d+)+(?:-jre)?", v.text)
        and v.text.startswith(major + ".")
    ]
    version = max(
        values, key=lambda v: tuple(int(x) for x in v.removesuffix("-jre").split("."))
    )
    url = f"{root}/{version}/{artifact}-{version}.jar"
    return {
        "version": version,
        "url": url,
        "sha256": hashlib.sha256(fetch(url)).hexdigest(),
    }


# Docker platform architecture -> official Node.js Linux archive architecture.
NODE_ARCH = {"arm64": "arm64", "amd64": "x64"}


def node_archive(node_versions, major, arch):
    """Newest official release of one Node major, bound to its SHA256."""
    node = next(
        v["version"] for v in node_versions if v["version"].startswith(f"v{major}.")
    )
    filename = f"node-{node}-linux-{NODE_ARCH[arch]}.tar.xz"
    sums = fetch(f"https://nodejs.org/dist/{node}/SHASUMS256.txt").decode()
    checksum = next(
        line.split()[0] for line in sums.splitlines() if line.split()[-1] == filename
    )
    return {
        "version": node,
        "url": f"https://nodejs.org/dist/{node}/{filename}",
        "sha256": checksum,
    }


def nuget(package, major):
    """Newest stable NuGet release of one major, bound to the catalog SHA512."""
    lower = package.lower()
    versions = json.loads(
        fetch(f"https://api.nuget.org/v3-flatcontainer/{lower}/index.json")
    )["versions"]
    version = max(
        (v for v in versions if re.fullmatch(rf"{major}\.\d+\.\d+", v)),
        key=lambda v: tuple(int(x) for x in v.split(".")),
    )
    leaf = json.loads(
        fetch(f"https://api.nuget.org/v3/registration5-semver1/{lower}/{version}.json")
    )
    catalog = json.loads(fetch(leaf["catalogEntry"]))
    data = fetch(leaf["packageContent"])
    sha512 = base64.b64encode(hashlib.sha512(data).digest()).decode()
    if catalog["packageHashAlgorithm"] != "SHA512" or catalog["packageHash"] != sha512:
        raise ValueError("NuGet package differs from its catalog hash: " + package)
    return {
        "version": version,
        "url": leaf["packageContent"],
        "sha256": hashlib.sha256(data).hexdigest(),
        "sha512": sha512,
    }


def resolve(node_versions, npm_version, arch):
    python_release = github("astral-sh/python-build-standalone", "releases/latest")
    python = next(
        a
        for a in python_release["assets"]
        if a["name"].startswith("cpython-3.11.")
        and a["name"].endswith("-x86_64-unknown-linux-gnu-install_only.tar.gz")
    )
    sources = {}
    for name, repo in [
        ("buildx", "docker/buildx"),
        ("compose", "docker/compose"),
        ("containerd", "containerd/containerd"),
    ]:
        release = github(repo, "releases/latest")
        commit = github(repo, "commits/" + release["tag_name"])["sha"]
        url = f"https://codeload.github.com/{repo}/tar.gz/{commit}"
        sources[name] = {
            "version": release["tag_name"],
            "commit": commit,
            "url": url,
            "sha256": hashlib.sha256(fetch(url)).hexdigest(),
        }
    return {
        # Runner slot -> patched Node release. The node20 slot keeps its
        # established Node 22 replacement; node24 follows the newest 24.x.
        "node_compat": node_archive(node_versions, 22, arch),
        "node24": node_archive(node_versions, 24, arch),
        "npm_version": npm_version,
        # Runner.Worker's ASP.NET helpers, matching upstream's unreleased bump.
        "nuget": {
            name: nuget(name, "8")
            for name in (
                "Microsoft.AspNetCore.WebUtilities",
                "Microsoft.Net.Http.Headers",
            )
        },
        "python": {**asset(python), "release": python_release["tag_name"]},
        "sources": sources,
        "go_modules": {
            "golang.org/x/net": "v0.62.0",
            "github.com/klauspost/compress": "v1.20.3",
            "github.com/go-jose/go-jose/v4": "v4.1.6",
            "github.com/moby/go-archive": "v0.3.3",
        },
        "jars": {
            name: maven(group, name, major)
            for group, name, major in [
                ("com.google.protobuf", "protobuf-java", "3"),
                ("org.jline", "jline-remote-telnet", "3"),
                ("org.jline", "jline-builtins", "3"),
                ("org.bouncycastle", "bcprov-jdk18on", "1"),
                ("com.google.guava", "guava", "33"),
            ]
        },
    }


def cache_release_asset(item, destination):
    """Authenticate on the host; put only checksum-verified bytes in the build."""
    url = urlsplit(item["url"])
    parts = url.path.split("/")
    if (
        url.netloc != "github.com"
        or len(parts) != 7
        or parts[3:5] != ["releases", "download"]
    ):
        raise ValueError("expected an official GitHub release asset")
    filename = unquote(parts[6])
    if Path(filename).name != filename:
        raise ValueError("invalid release asset filename")
    with tempfile.TemporaryDirectory(prefix="runner-release-asset-") as temporary:
        subprocess.run(
            [
                "gh",
                "release",
                "download",
                unquote(parts[5]),
                "--repo",
                "/".join(parts[1:3]),
                "--pattern",
                filename,
                "--dir",
                temporary,
            ],
            check=True,
        )
        data = (Path(temporary) / filename).read_bytes()
        if hashlib.sha256(data).hexdigest() != item["sha256"]:
            raise ValueError("release asset checksum mismatch")
        destination.mkdir(parents=True, exist_ok=True)
        (destination / item["sha256"]).write_bytes(data)


if __name__ == "__main__":
    inputs = json.loads(Path(sys.argv[1]).read_text())
    security = json.loads(inputs["security_inputs"])
    cache_release_asset(security["python"], Path(sys.argv[2]))
