#!/usr/bin/env bash
# Each Apple Container Linux VM owns this daemon and its entire workspace.
set -euo pipefail
: "${ACTIONS_RUNNER_INPUT_JITCONFIG:?JIT configuration is required}"
test "$(id -u)" = 0
dockerd --host unix:///var/run/docker.sock --group docker >/tmp/runner-dockerd.log 2>&1 &
daemon=$!
child=''
cleanup() {
	trap - EXIT
	[[ -z "$child" ]] || kill -TERM "$child" 2>/dev/null || true
	[[ -z "$child" ]] || wait "$child" 2>/dev/null || true
	# This daemon belongs to this VM and this job only. Let containerd and
	# its cgroups finish before Apple's VM runtime deletes the process.
	kill -TERM "$daemon" 2>/dev/null || true
	wait "$daemon" 2>/dev/null || true
}
trap cleanup EXIT
trap '[[ -z "$child" ]] || kill -TERM "$child" 2>/dev/null || true' TERM INT
ready=false
for _ in $(seq 1 120); do
	kill -0 "$daemon"
	if docker info >/dev/null 2>&1; then
		ready=true
		break
	fi
	sleep 0.5
done
if [[ "$ready" != true ]]; then
	echo 'Private job Docker daemon did not become ready.' >&2
	exit 1
fi
cd /opt/actions-runner
export HOME=/home/runner USER=runner LOGNAME=runner
runuser --preserve-environment -u runner -- ./run.sh &
child=$!
set +e
wait "$child"
status=$?
while kill -0 "$child" 2>/dev/null; do
	wait "$child"
	status=$?
done
child=''
cleanup
exit "$status"
