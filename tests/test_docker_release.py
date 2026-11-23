"""Exercise release integrity and failure gates without publishing an artifact."""

import importlib.util
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

REAL_RUN = subprocess.run

spec = importlib.util.spec_from_file_location(
    "docker_release", Path(__file__).resolve().parents[1] / "docker/release-image.py"
)
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


@pytest.fixture(params=["arm64"])
def arch(request):
    return request.param


@pytest.fixture
def candidate(tmp_path, monkeypatch, arch):
    monkeypatch.chdir(tmp_path)
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    for name in ("inputs.json", "inventory.json", "image.json"):
        (bundle / name).write_text("{}\n")
    (bundle / "toolchain-check.txt").write_text("passed\n")
    receipt = {
        "status": "qualified",
        "profile": "ubuntu-24.04-android",
        "source_commit": "a" * 40,
        "image": {"Id": "sha256:" + "1" * 64, "Os": "linux", "Architecture": arch},
        "evidence": {p.name: release.file_hash(p) for p in bundle.iterdir()},
    }
    (bundle / "qualification.json").write_text(json.dumps(receipt))
    state = {
        "calls": [],
        "release": None,
        "assets": {},
        "scan_failure": False,
        "signature_failure": False,
        "asset_failure": False,
        "branch": "main",
        "remote_failure": False,
        "platform_failure": False,
    }

    def command(*args, **kwargs):
        state["calls"].append(args)
        if args[:3] == ("git", "rev-parse", "HEAD"):
            return "a" * 40
        if args[:3] == ("git", "branch", "--show-current"):
            return state["branch"]
        if args[:3] == ("docker", "image", "inspect"):
            image_id = "1" * 64
            if state["remote_failure"] and str(args[3]).startswith("ghcr.io/"):
                image_id = "3" * 64
            return json.dumps(
                [
                    {
                        "Id": "sha256:" + image_id,
                        "Os": "linux",
                        "Architecture": (
                            "amd64"
                            if arch == "arm64" and state["platform_failure"]
                            else arch
                        ),
                        "RepoDigests": [
                            "ghcr.io/circlesac/runner-images/docker-ubuntu-24.04-android@sha256:"
                            + "2" * 64
                        ],
                    }
                ]
            )
        if args[:2] == ("gh", "api"):
            return (
                json.dumps([[state["release"]] if state["release"] else []])
                if "--slurp" in args
                else json.dumps(state["release"])
            )
        if args[0] == "npx":
            return "26.11.0"
        if args[:3] == ("gh", "attestation", "verify"):
            # actions/attest-sbom declares SPDX 2.3 with this versioned URI.
            # Match the real producer contract, including rejected type filters.
            if (
                str(args[args.index("--bundle") + 1]).endswith("sbom.sigstore.json")
                and args[args.index("--predicate-type") + 1]
                != "https://spdx.dev/Document/v2.3"
            ):
                raise subprocess.CalledProcessError(1, args)
            if state["signature_failure"]:
                raise subprocess.CalledProcessError(1, args)
            predicate = (
                receipt
                if "https://circles.ac/runner-images/docker-build/v1" in args
                else release.package_sbom(bundle / "sbom.spdx.json")
            )
            return json.dumps(
                [{"verificationResult": {"statement": {"predicate": predicate}}}]
            )
        raise AssertionError(args)

    def run(args, **kwargs):
        args = tuple(map(str, args))
        state["calls"].append(args)
        if args[0] == "syft":
            kwargs["stdout"].write(
                json.dumps(
                    {
                        "spdxVersion": "SPDX-2.3",
                        "SPDXID": "SPDXRef-DOCUMENT",
                        "documentNamespace": "https://example.com/sbom",
                        "packages": [],
                        "files": [],
                        "relationships": [],
                    }
                )
                + "\n"
            )
            if args[:2] == ("syft", "scan") and state["scan_failure"]:
                raise subprocess.CalledProcessError(2, args)
        elif args[:3] == ("gh", "release", "create"):
            notes = Path(args[args.index("--notes-file") + 1]).read_text()
            state["release"] = {
                "id": 1,
                "tag_name": args[3],
                "body": notes,
                "draft": True,
                "html_url": "https://example.invalid/release",
                "assets": [],
            }
        elif args[:3] == ("gh", "release", "upload"):
            paths = [Path(arg) for arg in args[args.index("--clobber") + 1 :]]
            state["assets"] = {p.name: p.read_bytes() for p in paths}
            state["release"]["assets"] = [
                {
                    "name": p.name,
                    "digest": "sha256:"
                    + ("0" * 64 if state["asset_failure"] else release.file_hash(p)),
                }
                for p in paths
            ]
        elif args[:3] == ("gh", "release", "edit"):
            state["release"]["draft"] = False
        elif args[:3] == ("gh", "release", "download"):
            name = args[args.index("--pattern") + 1]
            (Path(args[args.index("--dir") + 1]) / name).write_bytes(
                state["assets"][name]
            )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(release, "command", command)
    monkeypatch.setattr(release.subprocess, "run", run)
    return bundle, state


def sign(bundle):
    for name in ("provenance.sigstore.json", "sbom.sigstore.json"):
        (bundle / name).write_text('{"synthetic":true}\n')


def test_sbom_failure_never_pushes_or_creates_release(candidate):
    bundle, state = candidate
    state["scan_failure"] = True
    with pytest.raises(subprocess.CalledProcessError):
        release.prepare("example:local", bundle)
    assert not any(call[:2] == ("docker", "push") for call in state["calls"])
    assert state["release"] is None


@pytest.mark.parametrize(
    "fault", ["tamper", "missing", "symlink", "branch", "remote", "platform"]
)
def test_invalid_evidence_or_identity_never_creates_a_release(candidate, fault):
    bundle, state = candidate
    if fault == "tamper":
        (bundle / "inputs.json").write_text("changed")
    elif fault == "missing":
        receipt = json.loads((bundle / "qualification.json").read_text())
        del receipt["evidence"]["inventory.json"]
        (bundle / "qualification.json").write_text(json.dumps(receipt))
    elif fault == "symlink":
        (bundle / "inputs.json").unlink()
        (bundle / "inputs.json").symlink_to(bundle / "image.json")
    elif fault == "branch":
        state["branch"] = "feature"
    elif fault == "platform":
        state["platform_failure"] = True
    else:
        state["remote_failure"] = True
    with pytest.raises(ValueError):
        release.prepare("example:local", bundle)
    assert state["release"] is None


@pytest.mark.parametrize("fault", ["signature", "asset", "tamper-after-prepare"])
def test_publish_fails_closed_before_a_release_becomes_public(candidate, fault):
    bundle, state = candidate
    release.prepare("example:local", bundle)
    sign(bundle)
    if fault == "tamper-after-prepare":
        (bundle / "inventory.json").write_text("changed")
    else:
        state[f"{fault}_failure"] = True
    with pytest.raises((ValueError, subprocess.CalledProcessError)):
        release.publish(bundle)
    assert state["release"]["draft"]


def test_publish_and_replay_keep_original_signed_assets(candidate):
    bundle, state = candidate
    release.prepare("example:local", bundle)
    sign(bundle)
    release.publish(bundle)
    assert not state["release"]["draft"]
    identity = release.file_hash(bundle / "qualification.json")
    recorded = (
        Path("qualifications/docker/ubuntu-24.04-android") / identity / "release.json"
    )
    assert json.loads(recorded.read_text())["status"] == "released"
    original_assets = dict(state["assets"])
    original_pushes = len(
        [call for call in state["calls"] if call[:2] == ("docker", "push")]
    )
    (bundle / "sbom.spdx.json").write_text("changed transient evidence")
    release.prepare("example:local", bundle)
    release.publish(bundle)
    assert state["assets"] == original_assets
    assert (bundle / "sbom.spdx.json").read_bytes() == original_assets["sbom.spdx.json"]
    assert (
        len(
            [call for call in state["calls"] if call[:3] == ("gh", "release", "create")]
        )
        == 1
    )
    assert (
        len(
            [call for call in state["calls"] if call[:3] == ("gh", "release", "upload")]
        )
        == 1
    )
    assert (
        len([call for call in state["calls"] if call[:2] == ("docker", "push")])
        == original_pushes
    )


@pytest.mark.parametrize(
    ("arch", "prefix", "description"),
    [
        ("arm64", "docker-ubuntu-24.04-android@", "ARM64"),
        ("amd64", "docker-ubuntu-24.04-android-amd64@", "AMD64"),
    ],
    indirect=["arch"],
)
def test_each_architecture_publishes_its_own_digest_and_release_series(
    candidate, prefix, description
):
    bundle, state = candidate
    release.prepare("example:local", bundle)
    oneup = next(call for call in state["calls"] if call[0] == "npx")
    assert oneup[oneup.index("--tag-prefix") + 1] == prefix
    assert state["release"]["tag_name"] == prefix + "26.11.0"
    assert f"Ubuntu 24.04 {description} Android runner." in state["release"]["body"]
    push = next(call for call in state["calls"] if call[:2] == ("docker", "push"))
    assert push[2].startswith(
        "ghcr.io/circlesac/runner-images/docker-ubuntu-24.04-android:q-"
    )
    publication = json.loads((bundle / "publication.json").read_text())
    assert publication["reference"].startswith(
        "ghcr.io/circlesac/runner-images/docker-ubuntu-24.04-android@sha256:"
    )


@pytest.mark.parametrize("arch", ["riscv64"], indirect=True)
def test_unsupported_architecture_never_publishes(candidate):
    bundle, state = candidate
    with pytest.raises(ValueError, match="platform"):
        release.prepare("example:local", bundle)
    assert state["release"] is None


def test_durable_receipt_uses_real_git_push_and_repeat_is_noop(
    candidate, monkeypatch, tmp_path
):
    bundle, _ = candidate
    fake_run = release.subprocess.run

    def git(*args, cwd=tmp_path):
        return REAL_RUN(
            ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
        ).stdout.strip()

    remote = tmp_path.parent / (tmp_path.name + "-remote.git")
    git("init", "--bare", str(remote))
    git("init", "-b", "main")
    git("config", "user.name", "Example Maintainer")
    git("config", "user.email", "maintainer@example.com")
    (tmp_path / ".gitignore").write_text("bundle/\n")
    git("add", ".gitignore")
    git("commit", "-m", "initial")
    git("remote", "add", "origin", str(remote))
    git("push", "-u", "origin", "main")

    def with_real_git(args, **kwargs):
        return (
            REAL_RUN(args, **kwargs) if args[0] == "git" else fake_run(args, **kwargs)
        )

    monkeypatch.setattr(release.subprocess, "run", with_real_git)
    release.prepare("example:local", bundle)
    sign(bundle)
    release.publish(bundle)
    first = git("rev-parse", "HEAD")
    assert git("--git-dir", str(remote), "rev-parse", "refs/heads/main") == first
    assert git("status", "--porcelain") == ""
    release.publish(bundle)
    assert git("rev-parse", "HEAD") == first
    assert git("--git-dir", str(remote), "rev-parse", "refs/heads/main") == first


def test_sbom_preserves_go_symbols_and_runs_no_vulnerability_gate(candidate):
    bundle, state = candidate
    release.prepare("example:local", bundle)
    scan = next(call for call in state["calls"] if call[:2] == ("syft", "scan"))
    assert scan[-2:] == ("-o", "syft-json")
    assert "--config" in scan
    assert next(call for call in state["calls"] if call[:2] == ("syft", "convert"))
    # runner-images is exempt from the Circles vulnerability release gate.
    assert not any(call[0] == "grype" for call in state["calls"])
    assert not (bundle / "vulnerabilities.json").exists()
    assert (
        "capture-symbols: all"
        in Path(release.__file__).with_name("syft.yaml").read_text()
    )


def test_attestable_sbom_preserves_packages_and_binds_complete_file_inventory(tmp_path):
    full = tmp_path / "sbom.spdx.json"
    package = {
        "SPDXID": "SPDXRef-package",
        "name": "example",
        "versionInfo": "1.2.3",
        "filesAnalyzed": True,
        "hasFiles": ["SPDXRef-file"],
        "packageVerificationCode": {"packageVerificationCodeValue": "abc"},
        "checksums": [{"algorithm": "SHA256", "checksumValue": "def"}],
    }
    full.write_text(
        json.dumps(
            {
                "SPDXID": "SPDXRef-DOCUMENT",
                "documentNamespace": "https://example.com/full",
                "packages": [package],
                "files": [{"SPDXID": "SPDXRef-file", "fileName": "example.bin"}],
                "relationships": [
                    {
                        "spdxElementId": "SPDXRef-DOCUMENT",
                        "relationshipType": "DESCRIBES",
                        "relatedSpdxElement": "SPDXRef-package",
                    },
                    {
                        "spdxElementId": "SPDXRef-package",
                        "relationshipType": "CONTAINS",
                        "relatedSpdxElement": "SPDXRef-file",
                    },
                ],
            }
        )
    )
    original = full.read_bytes()
    release.write_attestation_sbom(tmp_path)
    signed = json.loads((tmp_path / "sbom.attestation.spdx.json").read_text())
    assert full.read_bytes() == original
    assert len(signed["packages"]) == 1
    assert signed["packages"][0]["versionInfo"] == "1.2.3"
    assert signed["packages"][0]["checksums"] == package["checksums"]
    assert signed["packages"][0]["filesAnalyzed"] is False
    assert "hasFiles" not in signed["packages"][0]
    assert "files" not in signed
    assert len(signed["relationships"]) == 1
    reference = signed["externalDocumentRefs"][-1]
    assert reference["checksum"] == {
        "algorithm": "SHA256",
        "checksumValue": release.file_hash(full),
    }
    assert reference["spdxDocument"] == "https://example.com/full"


def test_full_sbom_tampering_fails_before_signed_release_publication(candidate):
    bundle, state = candidate
    release.prepare("example:local", bundle)
    sign(bundle)
    full = bundle / "sbom.spdx.json"
    document = json.loads(full.read_text())
    document["files"].append({"SPDXID": "SPDXRef-injected", "fileName": "changed"})
    full.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="attestable SBOM"):
        release.publish(bundle)
    assert state["release"]["draft"] is True
    assert state["assets"] == {}


def test_oversized_package_inventory_fails_before_attestation(tmp_path):
    full = tmp_path / "sbom.spdx.json"
    full.write_text(
        json.dumps(
            {
                "documentNamespace": "https://example.com/full",
                "packages": [
                    {"SPDXID": "SPDXRef-package", "comment": "x" * (16 * 1024 * 1024)}
                ],
            }
        )
    )
    with pytest.raises(ValueError, match="service limit"):
        release.write_attestation_sbom(tmp_path)
    assert not (tmp_path / "sbom.attestation.spdx.json").exists()
