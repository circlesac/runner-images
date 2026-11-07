import copy
import json
import subprocess
from pathlib import Path

import pytest

from runner_images import rebuild


@pytest.fixture
def root(tmp_path):
    for name in (
        ".github/workflows/build-macos.yml",
        "snapshots/github-hosted/macos-26.json",
        "macos/accepted-missing/xcode.txt",
        "macos/accepted-missing/xcode-android.txt",
        "macos/build-image.sh",
        "runner_images/qualification.py",
        "scripts/commit-qualification.sh",
    ):
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("synthetic recipe")
    return tmp_path


@pytest.fixture
def api(monkeypatch):
    state = [
        {
            "number": 1,
            "body": "Implementation issue",
            "state": "open",
            "user": {"login": "example-user"},
        }
    ]
    writes = []

    def request(method, endpoint, payload=None):
        if method == "GET":
            return copy.deepcopy(state)
        writes.append((method, endpoint, payload))
        if method == "POST":
            state.append(
                {
                    "number": len(state) + 1,
                    **payload,
                    "state": "open",
                    "user": {"login": "github-actions[bot]", "id": 42},
                }
            )
        else:
            issue = next(i for i in state if endpoint.endswith("/" + str(i["number"])))
            issue.update(payload)
        return {}

    monkeypatch.setattr(rebuild, "gh", request)
    return state, writes


def publication(root, profile, identity=None):
    target = root / f"qualifications/tart/{profile}/example/publication.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {
                "input_id": identity or rebuild.input_id(root, profile),
                "status": "uploaded",
                "reference": "ghcr.io/example/image@sha256:example",
            }
        )
    )


def test_issue_lifecycle_dedup_upload_close_and_new_recipe_reopen(root, api):
    state, writes = api
    rebuild.reconcile(root)
    assert len(state) == 3  # implementation issue preserved, two profiles
    before = copy.deepcopy(state)
    writes.clear()
    rebuild.reconcile(root)
    assert state == before and writes == []
    publication(root, "xcode")
    rebuild.reconcile(root)
    assert state[1]["state"] == "closed"
    assert state[2]["state"] == "open"
    assert state[0] == before[0]
    writes.clear()
    rebuild.reconcile(root)
    assert not writes  # completion replay is a no-op
    (root / "macos/build-image.sh").write_text("new recipe")
    rebuild.reconcile(root)
    assert state[1]["state"] == "open"
    assert len(state) == 3  # reuse, never create another reminder


def test_old_build_cannot_close_new_request_and_failed_upload_stays_open(root, api):
    state, _ = api
    old = rebuild.input_id(root, "xcode")
    rebuild.reconcile(root)
    (root / "snapshots/github-hosted/macos-26.json").write_text("new reference")
    rebuild.reconcile(root)
    publication(root, "xcode", old)
    rebuild.reconcile(root)
    assert state[1]["state"] == "open"
    path = next((root / "qualifications/tart/xcode").glob("*/publication.json"))
    path.write_text(path.read_text().replace("uploaded", "failed"))
    rebuild.reconcile(root)
    assert state[1]["state"] == "open"


def test_repair_preexisting_duplicates_and_ignore_nonbot_markers(root, api):
    state, _ = api
    rebuild.reconcile(root)
    duplicate = copy.deepcopy(state[1])
    duplicate["number"] = 4
    state.append(duplicate)
    human = copy.deepcopy(duplicate)
    human.update(number=5, user={"login": "example-user"})
    state.append(human)
    rebuild.reconcile(root)
    assert state[1]["state"] == "open"
    assert state[3]["state"] == "closed"
    assert state[4]["state"] == "open"
    publication(root, "xcode")
    rebuild.reconcile(root)
    assert state[1]["state"] == "closed"


def test_already_uploaded_inputs_do_not_create_issue(root, api):
    state, _ = api
    for profile in ("xcode", "xcode-android"):
        publication(root, profile)
    rebuild.reconcile(root)
    assert len(state) == 1


def test_inputs_track_recipe_not_generated_records_or_linux(root):
    before = rebuild.input_id(root, "xcode")
    publication(root, "xcode")
    (root / "README.md").write_text("documentation")
    (root / "snapshots/github-hosted/ubuntu-24.04.json").write_text("Linux")
    assert rebuild.input_id(root, "xcode") == before
    (root / "scripts/commit-qualification.sh").write_text("changed uploader")
    assert rebuild.input_id(root, "xcode") != before
    with pytest.raises(ValueError):
        rebuild.input_id(root, "unknown")


def test_rest_pagination_and_invalid_response(monkeypatch):
    pages = iter([[{"number": i} for i in range(100)], [{"number": 101}]])
    calls = []

    def request(method, endpoint):
        calls.append(endpoint)
        return next(pages)

    monkeypatch.setattr(rebuild, "gh", request)
    assert len(rebuild.issues()) == 101
    assert "page=2" in calls[-1]
    monkeypatch.setattr(rebuild, "gh", lambda *args: {})
    with pytest.raises(ValueError):
        rebuild.issues()


def test_gh_boundary_preserves_json_and_propagates_failure(monkeypatch):
    monkeypatch.setattr(subprocess, "check_output", lambda *a, **kw: "[1]")
    assert rebuild.gh("GET", "example") == [1]
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, '{"number": 42}')

    monkeypatch.setattr(subprocess, "run", run)
    assert rebuild.gh("POST", "example", {"body": "line\nnext"}) == {"number": 42}
    assert json.loads(calls[0][1]["input"]) == {"body": "line\nnext"}

    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(403, ["gh"])

    monkeypatch.setattr(subprocess, "run", fail)
    with pytest.raises(subprocess.CalledProcessError):
        rebuild.gh("POST", "example", {})


def test_all_issue_writers_share_serialization_and_trusted_source():
    workflow = Path(".github/workflows/rebuild-issues.yml").read_text()
    assert "group: image-rebuild-issues" in workflow
    assert "cancel-in-progress: false" in workflow
    assert "ref: main" in workflow
    assert "workflow_run" in workflow and "!= 'pull_request'" in workflow
    # Multiple completion events and the schedule are coalesced by Actions;
    # the lifecycle tests above replay their actual handler against durable state.
