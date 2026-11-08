"""Repository static gate: uv run python scripts/check.py."""

import subprocess
import sys
from pathlib import Path

for command in (
    ["ruff", "format", "--check", "."],
    ["ruff", "check", "."],
    ["pyright"],
    ["shfmt", "-d", "scripts", "macos", "docker"],
    [
        "shellcheck",
        *map(str, sorted(Path("scripts").glob("*.sh"))),
        *map(str, sorted(Path("macos").rglob("*.sh"))),
        *map(str, sorted(Path("docker").glob("*.sh"))),
    ],
):
    result = subprocess.run(command, check=False)
    if result.returncode:
        sys.exit(result.returncode)
