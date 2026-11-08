#!/usr/bin/env bash
# The shared volume must be mounted at its real Docker-daemon mountpoint.
set -euo pipefail
: "${SHARED_DIR:?runscaler shared-volume must name the daemon-visible work root}"
: "${ACTIONS_RUNNER_INPUT_JITCONFIG:?JIT configuration is required}"
[[ "$SHARED_DIR" == /* && "$SHARED_DIR" != / && "$SHARED_DIR" != *..* ]]
[[ "$HOSTNAME" =~ ^[a-zA-Z0-9][a-zA-Z0-9._-]+$ ]]
root="$SHARED_DIR/$HOSTNAME"
# An existing directory belongs to a previous attempt and must be preserved.
mkdir "$root"
cp -a /opt/actions-runner/. "$root/"
cd "$root"
# Keep exec's signal semantics. runscaler removes the container afterwards;
# successful workspace cleanup is performed by the host, after mount read-back.
exec ./run.sh
