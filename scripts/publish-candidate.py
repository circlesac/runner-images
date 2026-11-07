"""Upload the exact qualified local Tart candidate, never an unverified VM."""

import argparse
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runner_images.qualification import read_object, record  # noqa: E402
from runner_images.registry import publish  # noqa: E402

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("bundle", type=Path)
args = parser.parse_args()
root = Path.cwd()
revision = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
# Independently validate all evidence before the first registry write.
record(args.bundle, root, revision)
image = read_object(args.bundle / "qualification.json")["build"]["image"]
tart_home = Path(os.environ.get("TART_HOME", str(Path.home() / ".tart")))
publish(args.bundle, root, tart_home / "vms" / image)
