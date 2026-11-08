#!/usr/bin/env bash
# Same local/CI build path; never registers a runner or changes an operating pool.
set -euo pipefail
cd "$(dirname "$0")/.."
image=${1:?Local destination image tag is required}
[[ "$image" =~ ^[a-zA-Z0-9][a-zA-Z0-9/:._-]*$ ]]
test -z "$(git status --porcelain --untracked-files=no)"
source_commit=$(git rev-parse HEAD)
mkdir -p artifacts/docker
bundle=$(mktemp -d artifacts/docker/build.XXXXXX)
python3 docker/resolve-inputs.py >"$bundle/inputs.json"
docker pull --platform linux/arm64 ghcr.io/actions/actions-runner:latest
base=$(docker image inspect ghcr.io/actions/actions-runner:latest --format '{{index .RepoDigests 0}}')
args=()
while IFS='=' read -r key value; do args+=(--build-arg "$key=$value"); done < <(
	python3 - "$bundle/inputs.json" <<'PY'
import json,sys
for key,value in json.load(open(sys.argv[1])).items(): print(key.upper()+'='+value)
PY
)
docker build --platform linux/arm64 --pull --build-arg "RUNNER_BASE=$base" "${args[@]}" -f docker/Dockerfile -t "$image" .
docker run --rm "$image" /usr/local/bin/verify-runner-toolchain >"$bundle/toolchain-check.txt" 2>&1
docker run --rm "$image" python3 /opt/runner-inventory/scripts/inventory.py >"$bundle/inventory.json"
docker image inspect "$image" --format '{{json .}}' | python3 -c 'import json,sys; x=json.load(sys.stdin); print(json.dumps({k:x[k] for k in ["Id","Architecture","Os"]}))' >"$bundle/image.json"
python3 - "$bundle" "$source_commit" "$base" <<'PY'
import hashlib,json,sys
from pathlib import Path
from runner_images.compare import load
bundle=Path(sys.argv[1]); inventory=load(bundle/'inventory.json'); image=json.loads((bundle/'image.json').read_text())
assert (inventory['os'],inventory['arch'])==('linux','arm64')
assert (image['Os'],image['Architecture'])==('linux','arm64')
assert 'native-arm64-android-toolchain-passed' in (bundle/'toolchain-check.txt').read_text()
receipt={'schema_version':1,'status':'qualified','source_commit':sys.argv[2],'base':sys.argv[3],'image':image,'profile':'ubuntu-24.04-android','evidence':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in bundle.iterdir() if p.name in ['inputs.json','inventory.json','toolchain-check.txt','image.json']}}
(bundle/'qualification.json').write_text(json.dumps(receipt,indent=2)+'\n')
PY
if [[ -n "${GITHUB_OUTPUT:-}" ]]; then printf 'bundle=%s\n' "$bundle" >>"$GITHUB_OUTPUT"; fi
printf 'Qualified %s; evidence: %s\n' "$image" "$bundle"
