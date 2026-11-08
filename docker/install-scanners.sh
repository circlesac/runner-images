#!/usr/bin/env bash
# Official release archives and SHA256 checksums, scoped to the caller's bin dir.
set -euo pipefail
bin=${1:?Destination bin directory}
mkdir -p "$bin"
case "$(uname -s)/$(uname -m)" in
Linux/aarch64) platform=linux_arm64 ;;
Linux/x86_64) platform=linux_amd64 ;;
Darwin/arm64) platform=darwin_arm64 ;;
*)
	echo 'Unsupported scanner platform' >&2
	exit 1
	;;
esac
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
for name in syft grype; do
	tag=$(gh api "repos/anchore/$name/releases/latest" --jq .tag_name)
	archive="${name}_${tag#v}_${platform}.tar.gz"
	checksums="${name}_${tag#v}_checksums.txt"
	mkdir "$work/$name"
	gh release download "$tag" --repo "anchore/$name" --pattern "$archive" --pattern "$checksums" --dir "$work/$name"
	(
		cd "$work/$name"
		grep " $archive\$" "$checksums" | shasum -a 256 -c -
	)
	tar -xzf "$work/$name/$archive" -C "$work/$name" "$name"
	install -m 755 "$work/$name/$name" "$bin/$name"
done
