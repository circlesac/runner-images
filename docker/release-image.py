"""Scan/push a qualified Docker image, then publish signed release evidence."""

import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path


def command(*args, **kwargs):
    return subprocess.check_output(list(args), text=True, **kwargs).strip()


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def checked_receipt(bundle):
    receipt = json.loads((bundle / "qualification.json").read_text())
    if receipt["status"] != "qualified" or receipt["profile"] != "ubuntu-24.04-android":
        raise ValueError("unsupported qualification")
    if set(receipt["evidence"]) != {
        "inputs.json",
        "inventory.json",
        "toolchain-check.txt",
        "image.json",
    }:
        raise ValueError("qualification evidence is incomplete")
    for name, digest in receipt["evidence"].items():
        path = bundle / name
        if path.is_symlink() or not path.is_file() or file_hash(path) != digest:
            raise ValueError("qualification evidence changed")
    return receipt


def existing(identity):
    pages = json.loads(
        command(
            "gh",
            "api",
            "--paginate",
            "--slurp",
            "repos/circlesac/runner-images/releases?per_page=100",
        )
    )
    matches = [
        r
        for page in pages
        for r in page
        if f"<!-- docker-qualification:{identity} -->" in (r.get("body") or "")
    ]
    if len(matches) > 1:
        raise ValueError("duplicate releases for one qualification")
    return matches[0] if matches else None


def prepare(image, bundle):
    receipt = checked_receipt(bundle)
    identity = file_hash(bundle / "qualification.json")
    head = command("git", "rev-parse", "HEAD")
    if (
        receipt["source_commit"] != head
        or command("git", "branch", "--show-current") != "main"
    ):
        raise ValueError("release must build the checked-out main revision")
    current = json.loads(command("docker", "image", "inspect", image))[0]
    if current["Id"] != receipt["image"]["Id"] or (
        current["Os"],
        current["Architecture"],
    ) != ("linux", "arm64"):
        raise ValueError("qualified image identity/platform changed")
    release = existing(identity)
    if release is not None and not release["draft"]:
        names = (
            "qualification.json",
            "inputs.json",
            "inventory.json",
            "image.json",
            "toolchain-check.txt",
            "publication.json",
            "sbom.spdx.json",
            "vulnerabilities.json",
            "provenance.sigstore.json",
            "sbom.sigstore.json",
        )
        by_name = {a["name"]: a for a in release["assets"]}
        for name in names:
            subprocess.run(
                [
                    "gh",
                    "release",
                    "download",
                    release["tag_name"],
                    "--repo",
                    "circlesac/runner-images",
                    "--pattern",
                    name,
                    "--dir",
                    str(bundle),
                    "--clobber",
                ],
                check=True,
            )
            if by_name.get(name, {}).get("digest") != "sha256:" + file_hash(
                bundle / name
            ):
                raise ValueError("published release asset checksum differs")
        publication = json.loads((bundle / "publication.json").read_text())
        if publication["qualification_sha256"] != identity:
            raise ValueError("published receipt identity differs")
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a") as output:
                output.write("already_released=true\n")
        print("Already published; reuse signed evidence and repair only Git recording.")
        return
    with (bundle / "sbom.spdx.json").open("w") as output:
        subprocess.run(
            ["syft", "scan", f"docker:{image}", "-o", "spdx-json"],
            stdout=output,
            check=True,
            timeout=900,
        )
    with (bundle / "vulnerabilities.json").open("w") as output:
        subprocess.run(
            [
                "grype",
                f"sbom:{bundle / 'sbom.spdx.json'}",
                "-o",
                "json",
                "--fail-on",
                "high",
            ],
            stdout=output,
            check=True,
            timeout=900,
        )
    target = f"ghcr.io/circlesac/runner-images/docker-ubuntu-24.04-android:q-{identity}"
    subprocess.run(["docker", "tag", image, target], check=True)
    subprocess.run(["docker", "push", target], check=True)
    subprocess.run(["docker", "pull", target], check=True)
    remote = json.loads(command("docker", "image", "inspect", target))[0]
    if remote["Id"] != current["Id"]:
        raise ValueError("registry image differs from the qualified image")
    immutable = next(
        d
        for d in remote["RepoDigests"]
        if d.startswith(target.split(":q-")[0] + "@sha256:")
    )
    if release is None:
        version_file = bundle / "version.go"
        version_file.write_text('package release\nconst Version = "0.0.0"\n')
        version = command(
            "npx",
            "--yes",
            "@circlesac/oneup@^26.10.0",
            "version",
            "--source",
            "git",
            "--tag-prefix",
            "docker-ubuntu-24.04-android@",
            "--target",
            str(version_file),
        ).splitlines()[-1]
        if not re.fullmatch(r"[0-9]{2}\.[0-9]{1,2}\.[0-9]+", version):
            raise ValueError("invalid CalVer")
        tag = f"docker-ubuntu-24.04-android@{version}"
        notes = bundle / "notes.md"
        notes.write_text(
            f"<!-- docker-qualification:{identity} -->\n"
            "Ubuntu 24.04 ARM64 Android runner.\n\n"
            f"Pull: `docker pull {immutable}`\n\nSource: `{head}`. "
            "This image is intended for a runscaler Docker provider. "
            "Registry visibility is managed separately.\n"
        )
        subprocess.run(
            [
                "gh",
                "release",
                "create",
                tag,
                "--repo",
                "circlesac/runner-images",
                "--draft",
                "--target",
                head,
                "--title",
                tag,
                "--notes-file",
                str(notes),
            ],
            check=True,
        )
    else:
        tag = release["tag_name"]
    publication = {
        "schema_version": 1,
        "qualification_sha256": identity,
        "reference": immutable,
        "source_commit": head,
        "tag": tag,
    }
    (bundle / "publication.json").write_text(json.dumps(publication, indent=2) + "\n")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as output:
            output.write(
                f"subject={immutable.split('@')[0]}\ndigest={immutable.split('@')[1]}\n"
            )
    print(immutable)


def publish(bundle):
    receipt = checked_receipt(bundle)
    publication = json.loads((bundle / "publication.json").read_text())
    identity = publication["qualification_sha256"]
    if file_hash(bundle / "qualification.json") != identity:
        raise ValueError("qualification changed before release publication")
    for name in (
        "provenance.sigstore.json",
        "sbom.sigstore.json",
        "sbom.spdx.json",
        "vulnerabilities.json",
    ):
        if not (bundle / name).is_file() or not (bundle / name).stat().st_size:
            raise ValueError("signed security evidence is incomplete")
    if publication["source_commit"] != receipt["source_commit"]:
        raise ValueError("publication source changed")
    for name, predicate_type, expected in (
        (
            "provenance.sigstore.json",
            "https://circles.ac/runner-images/docker-build/v1",
            receipt,
        ),
        (
            "sbom.sigstore.json",
            "https://spdx.dev/Document",
            json.loads((bundle / "sbom.spdx.json").read_text()),
        ),
    ):
        verified = json.loads(
            command(
                "gh",
                "attestation",
                "verify",
                "oci://" + publication["reference"],
                "--bundle",
                str(bundle / name),
                "--repo",
                "circlesac/runner-images",
                "--signer-workflow",
                "circlesac/runner-images/.github/workflows/release-docker.yml",
                "--source-ref",
                "refs/heads/main",
                "--predicate-type",
                predicate_type,
                "--format",
                "json",
            )
        )
        if not any(
            item.get("verificationResult", {}).get("statement", {}).get("predicate")
            == expected
            for item in verified
        ):
            raise ValueError("signed predicate differs from the release evidence")
    release = existing(identity)
    if release is None or release["tag_name"] != publication["tag"]:
        raise ValueError("matching release not found")
    assets = [
        bundle / name
        for name in (
            "qualification.json",
            "inputs.json",
            "inventory.json",
            "image.json",
            "toolchain-check.txt",
            "publication.json",
            "sbom.spdx.json",
            "vulnerabilities.json",
            "provenance.sigstore.json",
            "sbom.sigstore.json",
        )
    ]
    if release["draft"]:
        subprocess.run(
            [
                "gh",
                "release",
                "upload",
                publication["tag"],
                "--repo",
                "circlesac/runner-images",
                "--clobber",
                *map(str, assets),
            ],
            check=True,
        )
    readback = json.loads(
        command("gh", "api", f"repos/circlesac/runner-images/releases/{release['id']}")
    )
    by_name = {a["name"]: a for a in readback["assets"]}
    for asset in assets:
        if by_name.get(asset.name, {}).get("digest") != "sha256:" + file_hash(asset):
            raise ValueError("uploaded release asset checksum differs")
    subprocess.run(
        [
            "gh",
            "release",
            "edit",
            publication["tag"],
            "--repo",
            "circlesac/runner-images",
            "--draft=false",
        ],
        check=True,
    )
    readback = json.loads(
        command("gh", "api", f"repos/circlesac/runner-images/releases/{release['id']}")
    )
    if readback["draft"]:
        raise ValueError("release is not published")
    target = Path("qualifications/docker/ubuntu-24.04-android") / identity
    target.mkdir(parents=True, exist_ok=True)
    for name in (
        "qualification.json",
        "inputs.json",
        "inventory.json",
        "image.json",
        "toolchain-check.txt",
        "publication.json",
    ):
        source = bundle / name
        if (target / name).exists() and (
            target / name
        ).read_bytes() != source.read_bytes():
            raise ValueError("immutable qualification record already differs")
        (target / name).write_bytes(source.read_bytes())
    (target / "release.json").write_text(
        json.dumps(
            {
                "status": "released",
                **publication,
                "url": readback["html_url"],
                "assets": {a.name: file_hash(a) for a in assets},
            },
            indent=2,
        )
        + "\n"
    )
    subprocess.run(["git", "add", "--", str(target)], check=True)
    if subprocess.run(["git", "diff", "--cached", "--quiet"]).returncode:
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=github-actions[bot]",
                "-c",
                "user.email=41898282+github-actions[bot]@users.noreply.github.com",
                "commit",
                "-m",
                "chore: record released Docker runner image",
            ],
            check=True,
        )
        subprocess.run(["git", "fetch", "origin", "main"], check=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=github-actions[bot]",
                "-c",
                "user.email=41898282+github-actions[bot]@users.noreply.github.com",
                "rebase",
                "origin/main",
            ],
            check=True,
        )
        subprocess.run(["git", "push", "origin", "HEAD:main"], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["prepare", "publish"])
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--image")
    args = parser.parse_args()
    if args.phase == "prepare":
        if not args.image:
            parser.error("prepare requires --image")
        prepare(args.image, args.bundle)
    else:
        publish(args.bundle)


if __name__ == "__main__":
    main()
