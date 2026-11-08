"""Resolve official moving inputs before building; retain their checksums."""

import json
import xml.etree.ElementTree as ET
from urllib.request import urlopen


def fetch(url):
    with urlopen(url, timeout=90) as response:
        return response.read()


versions = json.loads(fetch("https://nodejs.org/dist/index.json"))
node = next(v["version"] for v in versions if v["lts"])
filename = f"node-{node}-linux-arm64.tar.xz"
checksums = fetch(f"https://nodejs.org/dist/{node}/SHASUMS256.txt").decode()
node_sha = next(
    line.split()[0] for line in checksums.splitlines() if line.split()[-1] == filename
)
repository = ET.fromstring(
    fetch("https://dl.google.com/android/repository/repository2-1.xml")
)
package = next(p for p in repository if p.attrib.get("path") == "cmdline-tools;latest")
archive = next(
    a for a in package.findall("archives/archive") if a.findtext("host-os") == "linux"
)
print(
    json.dumps(
        {
            "node_version": node,
            "node_sha256": node_sha,
            "android_url": "https://dl.google.com/android/repository/"
            + archive.findtext("complete/url"),
            "android_sha1": archive.findtext("complete/checksum"),
        },
        indent=2,
    )
)
