#!/usr/bin/env bash
# These execute inside the provisioning VM. Fresh-image boot tests are separate.
set -euo pipefail
exec > >(tee /tmp/runner-evidence/toolchain-check.txt) 2>&1
for command in gcc clang cmake ninja git git-lfs curl jq python3 node npm go java rustc cargo docker; do
	command -v "$command"
done
/opt/actions-runner/bin/Runner.Listener --version
python3 --version
node --version
go version
java -version
rustc --version
sudo docker info --format '{{.ServerVersion}}'
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
printf '#include <stdio.h>\nint main(void){puts("runner-smoke");return 0;}\n' >"$work/hello.c"
gcc "$work/hello.c" -o "$work/hello"
[[ $("$work/hello") == runner-smoke ]]
python3 -m venv "$work/venv"
"$work/venv/bin/python" -c 'import ssl, sqlite3, venv; assert ssl.OPENSSL_VERSION'
node -e 'require("node:assert").strictEqual(2 + 2, 4)'
printf 'package main\nimport "fmt"\nfunc main(){fmt.Print("runner-smoke")}\n' >"$work/hello.go"
[[ $(go run "$work/hello.go") == runner-smoke ]]
python3 - <<'PY'
import json
import sys
sys.path.insert(0, '/tmp/runner-source')
from runner_images.inventory import collect
with open('/tmp/runner-evidence/inventory.json', 'w') as stream:
    json.dump(collect(), stream, indent=2)
PY
sudo syft scan dir:/ \
	--exclude './proc/**' --exclude './sys/**' --exclude './dev/**' \
	--exclude './tmp/**' --exclude './var/log/**' --exclude './var/lib/cloud/**' \
	--exclude './var/lib/waagent/**' --exclude './root/**' \
	--exclude './home/**/.ssh/**' -o spdx-json | tee /tmp/runner-evidence/sbom.spdx.json >/dev/null
