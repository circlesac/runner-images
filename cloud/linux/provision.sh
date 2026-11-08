#!/usr/bin/env bash
# Shared maintained base profile, not the full GitHub-hosted software catalogue.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
sudo cloud-init status --wait
sudo apt-get update
sudo -E apt-get -y -o Dpkg::Options::=--force-confdef \
	-o Dpkg::Options::=--force-confold upgrade
sudo -E apt-get install -y --no-install-recommends \
	build-essential clang cmake ninja-build git curl wget jq ca-certificates gnupg \
	zip unzip xz-utils python3 python3-venv python3-pip \
	openjdk-21-jdk-headless
# Use Docker's signed upstream packages rather than the older distro bundle.
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL --retry 5 https://download.docker.com/linux/ubuntu/gpg | sudo tee /etc/apt/keyrings/docker.asc >/dev/null
sudo chmod a+r /etc/apt/keyrings/docker.asc
printf '%s\n' 'deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu noble stable' | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
sudo apt-get update
sudo -E apt-get install -y --no-install-recommends docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo python3 /tmp/runner-source/install-toolchains.py /usr/local /tmp/runner-evidence/toolchains.json
sudo ln -s /usr/local/go/bin/go /usr/local/bin/go
sudo ln -s /usr/local/go/bin/gofmt /usr/local/bin/gofmt
# Refresh mounted Snap packages as well as apt packages before measuring the image.
if command -v snap >/dev/null; then
	sudo snap refresh
	while read -r name revision; do
		sudo snap remove "$name" --revision="$revision"
	done < <(LC_ALL=C snap list --all | awk 'NR > 1 && $NF ~ /(^|,)disabled(,|$)/ {print $1, $3}')
fi
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
# The scanner is a temporary build dependency, not a shipped runner tool.
release=$(curl -fsSL --retry 5 https://api.github.com/repos/anchore/syft/releases/latest)
url=$(jq -er '.assets[] | select(.name | endswith("_linux_amd64.tar.gz")) | .browser_download_url' <<<"$release")
checksum=$(jq -er '.assets[] | select(.name | endswith("_linux_amd64.tar.gz")) | .digest | ltrimstr("sha256:")' <<<"$release")
curl -fsSL --retry 5 "$url" -o /tmp/syft.tar.gz
printf '%s  %s\n' "$checksum" /tmp/syft.tar.gz | sha256sum --check
mkdir -p /tmp/runner-tools
tar xzf /tmp/syft.tar.gz -C /tmp/runner-tools syft
