#!/usr/bin/env bash
# The shared volume must be mounted at its real Docker-daemon mountpoint.
set -euo pipefail
: "${SHARED_DIR:?runscaler shared-volume must name the daemon-visible work root}"
: "${ACTIONS_RUNNER_INPUT_JITCONFIG:?JIT configuration is required}"
[[ "$SHARED_DIR" == /* && "$SHARED_DIR" != / && "$SHARED_DIR" != *..* ]] || {
	echo "Invalid shared work root" >&2
	exit 2
}
[[ "$HOSTNAME" =~ ^[a-zA-Z0-9][a-zA-Z0-9._-]+$ ]] || {
	echo "Invalid runner hostname" >&2
	exit 2
}
root="$SHARED_DIR/$HOSTNAME"
# An existing directory belongs to a previous attempt and must be preserved.
mkdir "$root"
cp -a /opt/actions-runner/. "$root/"
cd "$root"
child=''
stopping=false
trap 'stopping=true; [[ -z "$child" ]] || kill -TERM "$child" 2>/dev/null || true' TERM INT
./run.sh &
child=$!
set +e
wait "$child"
status=$?
while kill -0 "$child" 2>/dev/null; do
	wait "$child"
	status=$?
done
set -e
if [[ "$status" == 0 && "$stopping" == false ]]; then
	# Only this attempt's completed directory can be removed. Preserve it if
	# the daemon cannot be queried or a stopped/running sibling still binds it.
	mounted=true
	if ids=$(docker ps --all --quiet) && sources=$(printf '%s\n' "$ids" | xargs -r docker inspect --format '{{range .Mounts}}{{println .Source}}{{end}}'); then
		mounted=false
		while IFS= read -r source; do
			case "$source" in "$root" | "$root"/*) mounted=true ;; esac
		done <<<"$sources"
	fi
	if [[ "$mounted" == false && ! -L "$root" && "$(pwd -P)" == "$root" ]]; then
		cd "$SHARED_DIR"
		rm -rf -- "$root"
	fi
fi
exit "$status"
