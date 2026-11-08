"""Exercise the real runner bootstrap across owned-directory lifecycle boundaries."""

import os
import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def runner(tmp_path):
    root = Path(__file__).resolve().parents[1]
    distribution = tmp_path / "distribution"
    distribution.mkdir()
    child = distribution / "run.sh"
    child.write_text(
        '#!/bin/bash\nmkdir _work\necho native-job > _work/probe\nexit "${RESULT:-0}"\n'
    )
    child.chmod(0o755)
    command = tmp_path / "runner.sh"
    command.write_text(
        (root / "docker/runner.sh")
        .read_text()
        .replace("/opt/actions-runner", str(distribution))
    )
    shared = tmp_path / "shared"
    shared.mkdir()
    binary = tmp_path / "bin"
    binary.mkdir()
    docker = binary / "docker"
    docker.write_text("""#!/bin/bash
if [[ "$1" == ps ]]; then echo example-container; exit "${PS_RESULT:-0}"; fi
if [[ "$1" == inspect ]]; then
  printf '%s\\n' "${BOUND_SOURCE:-}"; exit "${INSPECT_RESULT:-0}"
fi
exit 2
""")
    docker.chmod(0o755)
    env = dict(
        os.environ,
        PATH=str(binary) + ":" + os.environ["PATH"],
        HOSTNAME="example-runner",
        SHARED_DIR=str(shared),
        ACTIONS_RUNNER_INPUT_JITCONFIG="synthetic-jit-config",
    )
    return command, shared, env


def run(runner, **changes):
    command, _, env = runner
    return subprocess.run(
        ["bash", str(command)],
        env=dict(env, **changes),
        capture_output=True,
        text=True,
        timeout=15,
    )


def test_completed_directory_is_removed_and_repeat_can_start_again(runner):
    _, shared, _ = runner
    unrelated = shared / "another-runner"
    unrelated.mkdir()
    (unrelated / "keep").write_text("foreign")
    for _ in range(2):
        assert run(runner).returncode == 0
        assert not (shared / "example-runner").exists()
        assert (unrelated / "keep").read_text() == "foreign"


@pytest.mark.parametrize(
    "failure", [{"RESULT": "17"}, {"PS_RESULT": "1"}, {"INSPECT_RESULT": "1"}]
)
def test_failed_attempt_or_unverified_mounts_are_preserved(runner, failure):
    _, shared, _ = runner
    result = run(runner, **failure)
    assert result.returncode == (17 if "RESULT" in failure else 0)
    assert (shared / "example-runner/_work/probe").read_text() == "native-job\n"


def test_stopped_or_running_job_bind_blocks_cleanup(runner):
    _, shared, _ = runner
    assert (
        run(runner, BOUND_SOURCE=str(shared / "example-runner/_work")).returncode == 0
    )
    assert (shared / "example-runner/_work/probe").exists()


def test_existing_attempt_and_foreign_symlink_are_never_reused(runner, tmp_path):
    _, shared, _ = runner
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    (foreign / "keep").write_text("preserve")
    (shared / "example-runner").symlink_to(foreign, target_is_directory=True)
    assert run(runner).returncode != 0
    assert (foreign / "keep").read_text() == "preserve"
    assert not (foreign / "run.sh").exists()


@pytest.mark.parametrize(
    "changes",
    [
        {"SHARED_DIR": "/"},
        {"SHARED_DIR": "relative"},
        {"HOSTNAME": "../other"},
        {"ACTIONS_RUNNER_INPUT_JITCONFIG": ""},
    ],
)
def test_invalid_configuration_allocates_nothing(runner, changes):
    _, shared, _ = runner
    assert run(runner, **changes).returncode != 0
    assert list(shared.iterdir()) == []
