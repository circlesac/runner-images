"""Repository static gate: uv run python scripts/check.py."""

import subprocess
import sys

for command in (
    ["ruff", "format", "--check", "."],
    ["ruff", "check", "."],
    ["pyright"],
):
    result = subprocess.run(command, check=False)
    if result.returncode:
        sys.exit(result.returncode)
