"""Validate a qualification bundle against its trusted Git checkout and record it."""

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runner_images.qualification import record  # noqa: E402

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("bundle", type=Path)
parser.add_argument("repository", type=Path)
args = parser.parse_args()
try:
    revision = subprocess.check_output(
        ["git", "-C", str(args.repository), "rev-parse", "HEAD"], text=True
    ).strip()
    print(record(args.bundle, args.repository, revision).relative_to(args.repository))
except (OSError, ValueError, subprocess.CalledProcessError):
    print(
        "Qualification record rejected: evidence does not match trusted inputs.",
        file=sys.stderr,
    )
    sys.exit(2)
