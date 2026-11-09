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
