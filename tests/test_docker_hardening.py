"""Exercise replacement checksums and atomic shaded-library updates."""

import importlib.util
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("hardening", ROOT / "docker/harden.py")
hardening = importlib.util.module_from_spec(spec)

sys.path.insert(0, str(ROOT / "docker"))
spec.loader.exec_module(hardening)


def jar(path, content):
    with zipfile.ZipFile(path, "w") as output:
        for name, value in content.items():
            output.writestr(name, value)
    return path


def test_shaded_replacement_preserves_compiler_and_removes_old_version(
    tmp_path,
):
    target = jar(
        tmp_path / "compiler.jar",
        {
            "org/jetbrains/kotlin/Compiler.class": "original compiler",
            "com/google/protobuf/Old.class": "vulnerable",
            "META-INF/maven/com.google.protobuf/protobuf-java/pom.properties": (
                "version=2.6.1"
            ),
            "resources/data.txt": "retained",
        },
    )
    replacement = jar(
        tmp_path / "protobuf.jar",
        {
            "com/google/protobuf/New.class": "patched",
            "META-INF/maven/com.google.protobuf/protobuf-java/pom.properties": (
                "version=3.25.9"
            ),
            "META-INF/MANIFEST.MF": "dependency manifest",
        },
    )
    hardening.patch_jar(target, {"protobuf-java": replacement})
    with zipfile.ZipFile(target) as result:
        assert (
            result.read("org/jetbrains/kotlin/Compiler.class") == b"original compiler"
        )
        assert result.read("resources/data.txt") == b"retained"
        assert "com/google/protobuf/Old.class" not in result.namelist()
        assert result.read("com/google/protobuf/New.class") == b"patched"
        assert (
            result.read(
                "META-INF/maven/com.google.protobuf/protobuf-java/pom.properties"
            )
            == b"version=3.25.9"
        )
        assert "META-INF/MANIFEST.MF" not in result.namelist()


def test_signature_failure_preserves_original_archive(tmp_path):
    target = jar(
        tmp_path / "signed.jar",
        {"META-INF/SIGNER.SF": "signed", "com/google/protobuf/Old.class": "old"},
    )
    replacement = jar(
        tmp_path / "patched.jar", {"com/google/protobuf/New.class": "new"}
    )
    original = target.read_bytes()
    with pytest.raises(ValueError, match="signed"):
        hardening.patch_jar(target, {"protobuf-java": replacement})
    assert target.read_bytes() == original
    assert not list(tmp_path.glob("*.patched.jar"))


def test_checksum_mismatch_never_writes_replacement(tmp_path, monkeypatch):
    monkeypatch.setattr(hardening, "fetch", lambda _: b"corrupted")
    target = tmp_path / "download"
    with pytest.raises(ValueError, match="checksum"):
        hardening.download(
            {"url": "https://example.com/tool", "sha256": "a" * 64}, target
        )
    assert not target.exists()


def test_asset_without_registry_digest_is_rejected():
    from security_inputs import asset

    with pytest.raises(ValueError, match="SHA256"):
        asset({"browser_download_url": "https://example.com/tool"})


def test_maven_resolution_keeps_compatible_major_and_records_actual_checksum(
    monkeypatch,
):
    import hashlib

    import security_inputs

    metadata = (
        b"<metadata><versioning><versions>"
        b"<version>3.30.17</version><version>4.5.0</version>"
        b"<version>3.31.0-RC1</version>"
        b"</versions></versioning></metadata>"
    )
    calls = []

    def fetch(url):
        calls.append(url)
        return metadata if url.endswith("maven-metadata.xml") else b"verified jar"

    monkeypatch.setattr(security_inputs, "fetch", fetch)
    value = security_inputs.maven("org.example", "library", "3")
    assert value["version"] == "3.30.17"
    assert value["sha256"] == hashlib.sha256(b"verified jar").hexdigest()
    assert calls[-1].endswith("/3.30.17/library-3.30.17.jar")


def test_shaded_dependency_without_maven_metadata_still_remains_in_the_sbom(tmp_path):
    target = jar(tmp_path / "compiler.jar", {"com/google/protobuf/Old.class": "old"})
    replacement = jar(
        tmp_path / "protobuf.jar", {"com/google/protobuf/New.class": "fixed"}
    )
    original = target.read_bytes()
    with pytest.raises(ValueError, match="version evidence"):
        hardening.patch_jar(target, {"protobuf-java": replacement})
    assert target.read_bytes() == original
    hardening.patch_jar(
        target, {"protobuf-java": replacement}, {"protobuf-java": "3.25.9"}
    )
    with zipfile.ZipFile(target) as result:
        metadata = result.read(
            "META-INF/maven/com.google.protobuf/protobuf-java/pom.properties"
        )
        assert b"version=3.25.9" in metadata
        assert b"artifactId=protobuf-java" in metadata


def test_sdk_replacement_keeps_signed_bytes_and_updates_consumer_classpath(tmp_path):
    android = tmp_path / "sdk"
    old = (
        android
        / "external/org/bouncycastle/bcprov-jdk18on/1.80.2/bcprov-jdk18on-1.80.2.jar"
    )
    old.parent.mkdir(parents=True)
    jar(old, {"old.class": "vulnerable"})
    reference = str(old.relative_to(android))
    classpath = jar(
        android / "lint-classpath.jar",
        {"META-INF/MANIFEST.MF": f"Class-Path: {reference}\n", "keep.txt": "preserved"},
    )
    replacement = jar(
        tmp_path / "new.jar",
        {"META-INF/SIGNER.SF": "signature", "fixed.class": "patched"},
    )
    hardening.upgrade_sdk_jar(android, "bcprov-jdk18on", replacement, "1.86")
    new = (
        android
        / "external/org/bouncycastle/bcprov-jdk18on/1.86/bcprov-jdk18on-1.86.jar"
    )
    assert new.read_bytes() == replacement.read_bytes()
    assert not old.exists()
    with zipfile.ZipFile(classpath) as output:
        assert output.read("keep.txt") == b"preserved"
        assert reference.encode() not in output.read("META-INF/MANIFEST.MF")
        assert str(new.relative_to(android)).encode() in output.read(
            "META-INF/MANIFEST.MF"
        )


@pytest.mark.parametrize("corrupt", [False, True])
def test_host_asset_prefetch_checks_release_bytes_before_staging(
    tmp_path, monkeypatch, corrupt
):
    import hashlib

    import security_inputs

    expected = b"verified release bytes"
    digest = hashlib.sha256(expected).hexdigest()
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        directory = Path(command[command.index("--dir") + 1])
        (directory / "tool.tar.gz").write_bytes(b"corrupt" if corrupt else expected)

    monkeypatch.setattr(security_inputs.subprocess, "run", run)
    item = {
        "url": "https://github.com/example-org/example-tools/releases/download/v1.2.3/tool.tar.gz",
        "sha256": digest,
    }
    target = tmp_path / "staged"
    if corrupt:
        with pytest.raises(ValueError, match="checksum"):
            security_inputs.cache_release_asset(item, target)
        assert not target.exists()
    else:
        security_inputs.cache_release_asset(item, target)
        assert (target / digest).read_bytes() == expected
    assert calls[0][:4] == ["gh", "release", "download", "v1.2.3"]
    assert calls[0][calls[0].index("--repo") + 1] == "example-org/example-tools"


def test_cached_asset_is_verified_without_an_image_build_credential(
    tmp_path, monkeypatch
):
    import hashlib

    data = b"cached release"
    digest = hashlib.sha256(data).hexdigest()
    cache = tmp_path / digest
    cache.write_bytes(data)
    monkeypatch.setattr(hardening, "Path", lambda _: tmp_path)
    monkeypatch.setattr(
        hardening,
        "fetch",
        lambda _: pytest.fail(
            "cached release must not download inside the image build"
        ),
    )
    target = tmp_path / "extracted"
    item = {"url": "https://example.com/tool", "sha256": digest}
    hardening.download(item, target)
    assert target.read_bytes() == data
    cache.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="checksum"):
        hardening.download(item, target)
    assert target.read_bytes() == data


def nuget_catalog(monkeypatch, package_bytes, catalog_hash=None):
    import base64
    import hashlib
    import json

    import security_inputs

    digest = base64.b64encode(hashlib.sha512(package_bytes).digest()).decode()

    def fetch(url):
        if url.endswith("/index.json"):
            return json.dumps(
                {"versions": ["8.0.9", "8.0.34", "8.0.100-rc.1", "9.0.1"]}
            ).encode()
        if "/registration5-semver1/" in url:
            return json.dumps(
                {
                    "catalogEntry": "https://api.nuget.org/v3/catalog0/entry.json",
                    "packageContent": "https://api.nuget.org/example.8.0.34.nupkg",
                }
            ).encode()
        if url.endswith("/entry.json"):
            return json.dumps(
                {
                    "packageHashAlgorithm": "SHA512",
                    "packageHash": catalog_hash or digest,
                }
            ).encode()
        return package_bytes

    monkeypatch.setattr(security_inputs, "fetch", fetch)
    return security_inputs, digest


def test_nuget_resolution_keeps_major_and_binds_the_catalog_hash(monkeypatch):
    import hashlib

    security_inputs, digest = nuget_catalog(monkeypatch, b"verified package")
    value = security_inputs.nuget("Example.Package", "8")
    assert value["version"] == "8.0.34"
    assert value["sha512"] == digest
    assert value["sha256"] == hashlib.sha256(b"verified package").hexdigest()


def test_nuget_package_differing_from_catalog_is_rejected(monkeypatch):
    security_inputs, _ = nuget_catalog(monkeypatch, b"tampered", catalog_hash="x")
    with pytest.raises(ValueError, match="catalog hash"):
        security_inputs.nuget("Example.Package", "8")


def pe_with_version(major, minor, build, revision):
    import struct

    return (
        b"MZ-synthetic"
        + struct.pack(
            "<4I", 0xFEEF04BD, 0x10000, major << 16 | minor, build << 16 | revision
        )
        + b"tail"
    )


def test_runner_assembly_upgrade_rewrites_every_deps_reference(tmp_path, monkeypatch):
    import json

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    old = "8.0.25"
    deps = {
        "targets": {
            ".NETCoreApp,Version=v8.0": {},
            ".NETCoreApp,Version=v8.0/linux-x64": {
                "Runner.Worker/1.0.0": {"dependencies": {"Example.Web": old}},
                f"Example.Web/{old}": {
                    "dependencies": {"Example.Headers": old},
                    "runtime": {
                        "lib/net8.0/Example.Web.dll": {
                            "assemblyVersion": "8.0.0.0",
                            "fileVersion": "8.0.2526.1",
                        }
                    },
                },
                f"Example.Headers/{old}": {
                    "runtime": {
                        "lib/net8.0/Example.Headers.dll": {
                            "assemblyVersion": "8.0.0.0",
                            "fileVersion": "8.0.2526.1",
                        }
                    }
                },
            },
        },
        "libraries": {
            f"Example.{name}/{old}": {
                "type": "package",
                "sha512": "sha512-old",
                "path": f"example.{name.lower()}/{old}",
            }
            for name in ("Web", "Headers")
        },
    }
    (bin_dir / "Runner.Worker.deps.json").write_text(json.dumps(deps))
    packages, archives = {}, {}
    for name in ("Example.Web", "Example.Headers"):
        packages[name] = {"version": "8.0.34", "sha512": "new-" + name}
        archives[id(packages[name])] = jar(
            tmp_path / f"{name}.nupkg",
            {f"lib/net8.0/{name}.dll": pe_with_version(8, 0, 3426, 7)},
        )
    monkeypatch.setattr(
        hardening, "download", lambda item, destination: archives[id(item)]
    )
    hardening.upgrade_runner_assemblies(bin_dir, packages, tmp_path)
    text = (bin_dir / "Runner.Worker.deps.json").read_text()
    assert old not in text
    result = json.loads(text)
    target = result["targets"][".NETCoreApp,Version=v8.0/linux-x64"]
    assert target["Runner.Worker/1.0.0"]["dependencies"]["Example.Web"] == "8.0.34"
    web = target["Example.Web/8.0.34"]
    assert web["dependencies"]["Example.Headers"] == "8.0.34"
    assert web["runtime"]["lib/net8.0/Example.Web.dll"]["fileVersion"] == "8.0.3426.7"
    library = result["libraries"]["Example.Headers/8.0.34"]
    assert library["sha512"] == "sha512-new-Example.Headers"
    assert library["path"] == "example.headers/8.0.34"
    assert (bin_dir / "Example.Web.dll").read_bytes() == pe_with_version(8, 0, 3426, 7)


def test_runner_assembly_upgrade_refuses_an_unexpected_reference(tmp_path):
    import json

    (tmp_path / "Runner.Worker.deps.json").write_text(
        json.dumps({"targets": {}, "libraries": {}})
    )
    with pytest.raises(ValueError, match="exactly one"):
        hardening.upgrade_runner_assemblies(
            tmp_path, {"Example.Web": {"version": "8.0.34"}}, tmp_path
        )
    with pytest.raises(ValueError, match="fixed file version"):
        hardening.file_version(b"MZ-no-version")
