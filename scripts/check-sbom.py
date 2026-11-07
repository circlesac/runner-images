"""Exercise both real guest exclusion policies against Syft's directory resolver."""

import argparse
import json
import re
import subprocess
import tempfile
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("syft", nargs="?", default="syft")
args = parser.parse_args()
for recipe, excluded in (
    ("cloud/linux/verify.sh", "proc"),
    ("macos/image/collect-sbom.sh", "System"),
):
    source = Path(recipe).read_text()
    patterns = re.findall(r"--exclude\s+'([^']+)'", source)
    assert patterns, f"{recipe}: exclusion policy is missing"
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        for folder, name in (("usr", "kept-example"), (excluded, "blocked-example")):
            metadata = (
                root
                / folder
                / "lib/python3.12/site-packages"
                / (name.replace("-", "_") + "-1.0.0.dist-info/METADATA")
            )
            metadata.parent.mkdir(parents=True)
            metadata.write_text(
                f"Metadata-Version: 2.1\nName: {name}\nVersion: 1.0.0\n"
            )
        command = [args.syft, "scan", f"dir:{root}", "-o", "spdx-json"]
        for pattern in patterns:
            command += ["--exclude", pattern]
        scope = re.search(r"--scope\s+(\S+)", source)
        if scope:
            command += ["--scope", scope.group(1)]
        result = subprocess.run(
            command, check=True, capture_output=True, text=True, timeout=120
        )
        sbom = json.loads(result.stdout)
        packages = {package["name"] for package in sbom["packages"]}
        assert "kept-example" in packages, packages
        assert "blocked-example" not in packages, packages
    print(
        f"{recipe}: actual Syft scan retains installed software "
        "and excludes transient data"
    )
