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


@pytest.fixture
def candidate(tmp_path, monkeypatch):
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
        "image": {"Id": "sha256:" + "1" * 64},
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
                        "Architecture": "arm64",
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
            if state["signature_failure"]:
                raise subprocess.CalledProcessError(1, args)
            predicate = (
                receipt
                if "https://circles.ac/runner-images/docker-build/v1" in args
                else json.loads((bundle / "sbom.spdx.json").read_text())
            )
            return json.dumps(
                [{"verificationResult": {"statement": {"predicate": predicate}}}]
            )
        raise AssertionError(args)

    def run(args, **kwargs):
        args = tuple(map(str, args))
        state["calls"].append(args)
        if args[0] in ("syft", "grype"):
            kwargs["stdout"].write("{}\n")
            if args[0] == "grype" and state["scan_failure"]:
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


def test_security_failure_never_pushes_or_creates_release(candidate):
    bundle, state = candidate
    state["scan_failure"] = True
    with pytest.raises(subprocess.CalledProcessError):
        release.prepare("example:local", bundle)
    assert not any(call[:2] == ("docker", "push") for call in state["calls"])
    assert state["release"] is None


@pytest.mark.parametrize("fault", ["tamper", "missing", "symlink", "branch", "remote"])
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
