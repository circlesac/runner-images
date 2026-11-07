import fcntl
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from test_registry import registry_fixture

from runner_images import local_build, registry
from runner_images.qualification import digest, finalize, qualify, record
from runner_images.rebuild import input_id


@pytest.fixture
def local(example, monkeypatch):
    root, seed, image_directory, metadata = example
    workflow = root / ".github/workflows/release-tart.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text("synthetic finalizer")
    (seed / "sbom.spdx.json").write_text(
        json.dumps({"spdxVersion": "SPDX-2.3", "packages": [{"name": "example-tool"}]})
    )
    monkeypatch.setattr(local_build.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(local_build.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(local_build.time, "sleep", lambda seconds: None)
    state = {
        "calls": [],
        "runs": [],
        "fail": "",
        "dirty": False,
        "occupied": False,
        "release_done": False,
    }

    def invoke(command, directory, env, *, output=False):
        state["calls"].append(command)
        if command[:3] == ["gh", "auth", "token"]:
            return "synthetic-secret"
        if command[:3] == ["gh", "api", "user"]:
            return "example-user"
        if command[:3] == ["git", "status", "--porcelain"]:
            return " M source" if state["dirty"] else ""
        if command[:2] == ["git", "rev-parse"]:
            return metadata["source_commit"]
        if command[:2] == ["tart", "list"]:
            if state["occupied"]:
                state["occupied"] = False
                return json.dumps([{"State": "running"}] * 2)
            return "[]"
        if command[:2] == ["bash", "macos/build-image.sh"]:
            image = command[2]
            bundle = root / "artifacts/tart" / image
            bundle.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(seed, bundle)
            if state["fail"] == "build":
                raise subprocess.CalledProcessError(1, command)
            qualify(
                bundle, image_directory, {**metadata, "image": image, "run_url": ""}
            )
            finalize(bundle, image_directory)
        if command[:2] == ["python3", "scripts/publish-candidate.py"]:
            if state["fail"] == "upload":
                raise subprocess.CalledProcessError(1, command)
            bundle = Path(command[2])
            registry_fixture(
                monkeypatch,
                digest(bundle / "qualification.json"),
                input_id(root, "xcode"),
            )
            monkeypatch.setattr(registry.subprocess, "run", lambda *a, **kw: None)
            registry.publish(bundle, root, image_directory)
        if command[:2] == ["bash", "scripts/commit-qualification.sh"]:
            bundle = Path(command[2])
            destination = record(bundle, root, metadata["source_commit"])
            registry.record_publication(bundle, root, destination)
            if state["fail"] == "record":
                raise subprocess.CalledProcessError(1, command)
        if command[:3] == ["gh", "run", "list"]:
            return "[]" if state.get("invisible") else json.dumps(state["runs"])
        if command[:3] == ["gh", "workflow", "run"]:
            key = command[-1].split("=", 1)[1]
            state["runs"] = [
                {
                    "databaseId": 12345,
                    "displayTitle": f"Release Tart {key}",
                    "status": "in_progress",
                    "conclusion": "",
                }
            ]
        if command[:3] == ["gh", "run", "rerun"]:
            state["runs"][0]["status"] = "in_progress"
        if command[:3] == ["gh", "run", "watch"]:
            if state["fail"] == "release":
                state["runs"][0].update(status="completed", conclusion="failure")
                raise subprocess.CalledProcessError(1, command)
            state["runs"][0].update(status="completed", conclusion="success")
            path = next((root / "qualifications/tart/xcode").glob("*/publication.json"))
            released = {
                **json.loads(path.read_text()),
                "status": "released",
                "url": "https://example.com/release",
            }
            path.with_name("release.json").write_text(json.dumps(released))
        return ""

    monkeypatch.setattr(local_build, "invoke", invoke)
    return root, state


def count(state, prefix):
    return sum(command[: len(prefix)] == prefix for command in state["calls"])


def test_one_invocation_finishes_and_duplicate_invocation_is_noop(local, capsys):
    root, state = local
    state["occupied"] = True
    local_build.rebuild(root, "xcode")
    journal = json.loads((root / "artifacts/rebuild.json").read_text())
    assert journal["done"] and journal["recorded"]
    assert count(state, ["bash", "macos/build-image.sh"]) == 1
    assert count(state, ["gh", "workflow", "run"]) == 1
    local_build.rebuild(root, "xcode")
    assert count(state, ["bash", "macos/build-image.sh"]) == 1
    assert count(state, ["gh", "workflow", "run"]) == 1
    output = capsys.readouterr().out
    assert "Already released" in output
    assert "synthetic-secret" not in output
    assert all(
        "synthetic-secret" not in " ".join(command) for command in state["calls"]
    )


@pytest.mark.parametrize("failure", ["build", "upload", "record", "release"])
def test_repeating_same_command_resumes_failed_stage_without_duplicate_release(
    local, failure
):
    root, state = local
    state["fail"] = failure
    with pytest.raises(subprocess.CalledProcessError):
        local_build.rebuild(root, "xcode")
    first = json.loads((root / "artifacts/rebuild.json").read_text())
    assert not first.get("done")
    state["fail"] = ""
    local_build.rebuild(root, "xcode")
    assert json.loads((root / "artifacts/rebuild.json").read_text())["done"]
    assert count(state, ["bash", "macos/build-image.sh"]) == (
        2 if failure == "build" else 1
    )
    assert count(state, ["gh", "workflow", "run"]) == 1
    if failure == "release":
        assert count(state, ["gh", "run", "rerun"]) == 1


def test_host_source_and_concurrent_invocation_guard(local, monkeypatch):
    root, state = local
    with pytest.raises(ValueError, match="unknown"):
        local_build.rebuild(root, "unknown")
    monkeypatch.setattr(local_build.platform, "system", lambda: "Linux")
    with pytest.raises(ValueError, match="Apple Silicon"):
        local_build.rebuild(root, "xcode")
    monkeypatch.setattr(local_build.platform, "system", lambda: "Darwin")
    (root / "artifacts").mkdir()
    with (root / "artifacts/rebuild.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            local_build.rebuild(root, "xcode")
    state["dirty"] = True
    with pytest.raises(ValueError, match="uncommitted"):
        local_build.rebuild(root, "xcode")
    assert not count(state, ["bash", "macos/build-image.sh"])


def test_lost_dispatch_response_reconnects_to_existing_run(local):
    root, state = local
    state["fail"] = "release"
    with pytest.raises(subprocess.CalledProcessError):
        local_build.rebuild(root, "xcode")
    journal = root / "artifacts/rebuild.json"
    value = json.loads(journal.read_text())
    value.pop("dispatched")
    journal.write_text(json.dumps(value))
    state["fail"] = ""
    local_build.rebuild(root, "xcode")
    assert count(state, ["gh", "workflow", "run"]) == 1


def test_invoke_streaming_and_machine_output(tmp_path):
    assert (
        local_build.invoke(
            ["python3", "-c", 'print("synthetic")'], tmp_path, {}, output=True
        )
        == "synthetic"
    )
    assert local_build.invoke(["python3", "-c", "pass"], tmp_path, {}) == ""


def test_dispatch_visibility_timeout_preserves_request_and_reconnects(
    local, monkeypatch
):
    root, state = local
    state["invisible"] = True
    times = iter([0, 0, 121])
    monkeypatch.setattr(local_build.time, "monotonic", lambda: next(times))
    with pytest.raises(TimeoutError, match="requested"):
        local_build.rebuild(root, "xcode")
    assert count(state, ["gh", "workflow", "run"]) == 1
    state["invisible"] = False
    local_build.rebuild(root, "xcode")
    assert count(state, ["gh", "workflow", "run"]) == 1
    assert json.loads((root / "artifacts/rebuild.json").read_text())["done"]
