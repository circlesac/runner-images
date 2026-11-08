import hashlib
import io
import json
import runpy
import tarfile
from pathlib import Path

import pytest


@pytest.fixture
def installer():
    return runpy.run_path("cloud/linux/install-toolchains.py")


def test_resolve_chooses_official_stable_linux_archives(installer, monkeypatch):
    documents = {
        "https://nodejs.org/dist/index.json": json.dumps(
            [
                {"version": "v99.0.0-rc.1", "lts": "Example"},
                {"version": "v26.1.0", "lts": False},
                {"version": "v24.1.0", "lts": "Example"},
            ]
        ),
        "https://nodejs.org/dist/v24.1.0/SHASUMS256.txt": "a" * 64
        + "  node-v24.1.0-linux-x64.tar.xz\n",
        "https://go.dev/dl/?mode=json": json.dumps(
            [
                {"version": "go9.0rc1", "stable": False},
                {
                    "version": "go1.25.0",
                    "stable": True,
                    "files": [
                        {"os": "linux", "arch": "arm64", "kind": "archive"},
                        {
                            "os": "linux",
                            "arch": "amd64",
                            "kind": "archive",
                            "filename": "go1.25.0.linux-amd64.tar.gz",
                            "sha256": "b" * 64,
                        },
                    ],
                },
            ]
        ),
        "https://static.rust-lang.org/dist/channel-rust-stable.toml": "\n".join(
            f'[pkg.{name}]\nversion = "1.90.0 (example)"\n'
            f"[pkg.{name}.target.x86_64-unknown-linux-gnu]\n"
            f'xz_url = "https://static.rust-lang.org/dist/{name}.tar.xz"\n'
            f'xz_hash = "{"c" * 64}"\n'
            for name in ("rustc", "rust-std", "cargo")
        ),
        "https://api.github.com/repos/git-lfs/git-lfs/releases/latest": json.dumps(
            {
                "tag_name": "v3.0.0",
                "assets": [
                    {
                        "name": "git-lfs-linux-amd64-v3.0.0.tar.gz",
                        "browser_download_url": "https://github.com/git-lfs/example.tar.gz",
                        "digest": "sha256:" + "d" * 64,
                    }
                ],
            }
        ),
    }
    monkeypatch.setitem(
        installer["resolve"].__globals__, "read", lambda url: documents[url].encode()
    )
    result = installer["resolve"]()
    assert result["node"]["version"] == "v24.1.0"
    assert result["go"]["url"].endswith("linux-amd64.tar.gz")
    assert result["git-lfs"]["sha256"] == "d" * 64
    assert set(result) == {"node", "go", "git-lfs", "rustc", "rust-std", "cargo"}


def test_unpack_rejects_tampering_before_extraction(installer, tmp_path, monkeypatch):
    def download(command, **kwargs):
        Path(command[-1]).write_bytes(b"tampered archive")

    monkeypatch.setattr(installer["subprocess"], "run", download)
    with pytest.raises(ValueError, match="checksum mismatch"):
        installer["unpack"](
            {"url": "https://example.com/tool.tar.gz", "sha256": "a" * 64}, tmp_path
        )
    assert not (tmp_path / "extracted").exists()


def test_unpack_keeps_relative_executable_symlinks(installer, tmp_path, monkeypatch):
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w:gz") as archive:
        entry = tarfile.TarInfo("node/lib/npm.js")
        entry.size = 4
        archive.addfile(entry, io.BytesIO(b"test"))
        link = tarfile.TarInfo("node/bin/npm")
        link.type = tarfile.SYMTYPE
        link.linkname = "../lib/npm.js"
        archive.addfile(link)
    content = data.getvalue()

    def download(command, **kwargs):
        Path(command[-1]).write_bytes(content)

    monkeypatch.setattr(installer["subprocess"], "run", download)
    root = installer["unpack"](
        {
            "url": "https://example.com/tool.tar.gz",
            "sha256": hashlib.sha256(content).hexdigest(),
        },
        tmp_path,
    )
    assert (root / "node/bin/npm").is_symlink()
    assert (root / "node/bin/npm").read_bytes() == b"test"


def test_unpack_recovers_from_a_real_interrupted_download(installer, tmp_path):
    import http.server
    import threading

    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w:gz") as archive:
        entry = tarfile.TarInfo("tool/version")
        entry.size = 3
        archive.addfile(entry, io.BytesIO(b"1.0"))
    content = data.getvalue()

    class Handler(http.server.BaseHTTPRequestHandler):
        attempts = 0

        def do_GET(self):
            Handler.attempts += 1
            partial = Handler.attempts == 1
            self.send_response(200)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(content[:8] if partial else content)
            self.close_connection = True

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        root = installer["unpack"](
            {
                "url": f"http://127.0.0.1:{server.server_port}/tool.tar.gz",
                "sha256": hashlib.sha256(content).hexdigest(),
            },
            tmp_path,
        )
        assert (root / "tool/version").read_bytes() == b"1.0"
        assert Handler.attempts == 2
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
