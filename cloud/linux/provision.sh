#!/usr/bin/env bash
# Shared maintained base profile, not the full GitHub-hosted software catalogue.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
sudo cloud-init status --wait
sudo apt-get update
sudo -E apt-get -y -o Dpkg::Options::=--force-confdef \
	-o Dpkg::Options::=--force-confold upgrade
sudo -E apt-get install -y --no-install-recommends \
	build-essential clang cmake ninja-build git git-lfs curl wget jq ca-certificates \
	zip unzip xz-utils python3 python3-venv python3-pip nodejs npm golang-go \
	openjdk-21-jdk-headless rustc cargo docker.io
sudo systemctl enable --now docker
sudo usermod -aG docker runnerbuilder
sudo mkdir -p /opt/actions-runner
sudo chown runnerbuilder:runnerbuilder /opt/actions-runner
cd /opt/actions-runner
release=$(curl -fsSL --retry 5 https://api.github.com/repos/actions/runner/releases/latest)
url=$(jq -er '.assets[] | select(.name | test("^actions-runner-linux-x64-.*tar.gz$")) | .browser_download_url' <<<"$release")
checksum=$(jq -er '.assets[] | select(.name | test("^actions-runner-linux-x64-.*tar.gz$")) | .digest | ltrimstr("sha256:")' <<<"$release")
curl -fsSL --retry 5 "$url" -o /tmp/actions-runner.tar.gz
printf '%s  %s\n' "$checksum" /tmp/actions-runner.tar.gz | sha256sum --check
tar xzf /tmp/actions-runner.tar.gz
sudo ./bin/installdependencies.sh
# No repository/organization registration, tokens, or cloud identity in the VM.
release=$(curl -fsSL --retry 5 https://api.github.com/repos/anchore/syft/releases/latest)
url=$(jq -er '.assets[] | select(.name | endswith("_linux_amd64.deb")) | .browser_download_url' <<<"$release")
checksum=$(jq -er '.assets[] | select(.name | endswith("_linux_amd64.deb")) | .digest | ltrimstr("sha256:")' <<<"$release")
curl -fsSL --retry 5 "$url" -o /tmp/syft.deb
printf '%s  %s\n' "$checksum" /tmp/syft.deb | sha256sum --check
sudo dpkg -i /tmp/syft.deb
