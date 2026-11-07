# Runner images

Inspect and compare GitHub Actions runner images on local machines and cloud VMs. The initial implementation measures actual capabilities on Linux and macOS; it does not provision GCP/Azure machines or claim complete version/runtime equivalence.

## What is measured

- Executable names reachable on `PATH`.
- Tool-cache families with a setup-action `.complete` marker.
- Image environment variable names, excluding runner/job/session metadata. Environment values, home directories, hostnames and full executable paths are not recorded.
- OS, architecture and optional `ImageOS`/`ImageVersion` provenance. Only equal OS/architecture pairs can be compared.

Comparison fails for unexpected missing capabilities and stale allowlist entries. Extra capabilities are reported without failing. An executable name proves discoverability; product tests and version checks still belong to the image consumer.

## Run locally

Python 3.11 or newer is the only runtime dependency. Run the same collector on each machine; it needs neither cloud credentials nor root access.

```sh
python3 scripts/inventory.py > candidate.json
python3 scripts/compare.py reference.json candidate.json
python3 scripts/compare.py reference.json candidate.json --allowlist accepted-missing.txt
```

Output is JSON. Comparison exits 0 when compatible, 1 for capability differences, and 2 for invalid/unreadable input or mismatched platforms. The allowlist is a reviewed text file, one record per line, such as `cmd example-tool` or `cache ExampleRuntime`. Missing and stale exceptions are checked in both directions. Keep candidate inventories and company-specific exception policies in their owning repositories.

## GitHub Actions

The [Hosted reference inventories](../../actions/workflows/reference.yml) workflow captures standard `ubuntu-24.04` and `macos-26` images before any setup action changes their toolchain. It runs on pull requests, pushes to main, or manual dispatch. Artifacts are `reference-ubuntu-24.04` and `reference-macos-26`, each containing `inventory.json`, retained for 30 days. They are generated evidence and are not committed.

The root composite Action collects a candidate:

```yaml
- uses: circlesac/runner-images@VERIFIED_COMMIT
  with:
    output: candidate.json
```

Compare after downloading the matching reference artifact:

```yaml
- uses: circlesac/runner-images/compare@VERIFIED_COMMIT
  with:
    reference: reference/inventory.json
    candidate: candidate.json
    allowlist: accepted-missing.txt
```

Replace `VERIFIED_COMMIT` with the reviewed revision. Existing consumers should download a reference from a successful **push to main** run of `reference.yml`, inspect its head revision and platform, and retain the selected run ID with their test evidence. For example, use `gh run list --repo circlesac/runner-images --workflow reference.yml --branch main --event push --status success`, then `gh run download RUN_ID --repo circlesac/runner-images --name reference-macos-26 --dir reference`. Authenticated artifact download needs a token that can read this public repository's Actions artifacts; do not grant write or administration permissions. No token is needed by the collector or comparer themselves.

Candidate machines run in their owning private workflows. This repository does not accept public PR code on a private self-hosted runner and never uploads private candidate inventories. Reference collection uses standard GitHub-hosted public-repository runners; cloud VM costs and larger-runner billing are separate.

## Development and verification

```sh
uv sync --frozen
uv run python scripts/check.py
uv run python scripts/test.py
uv run ruff format .
```

The test command runs formatting, lint, strict type checking and behavioral tests with branch coverage (minimum 80%). The same commands run on Linux and macOS. Tests exercise real executable files/symlinks, cache completion, secret-value omission, repeated collection, incompatible platforms, invalid snapshots, stale allowlists and process exit codes.

| Tooling | Version at selection (2026-11-07) | Registry release | Reason |
| --- | --- | --- | --- |
| Ruff | 0.17.4 | 2026-11-05 | Native formatter and linter; selected over Black 26.10.1 (2026-10-10), which is formatter-only and requires a second lint tool |
| pytest / pytest-cov | 9.2.0 / 7.1.0 | 2026-10-27 / 2026-03-21 | Behavioral tests and branch coverage |
| Pyright | 1.1.414 | 2026-09-10 | Strict checks for the typed collection/comparison boundary |

Development dependencies use minimum compatible ranges and `uv.lock`; runtime collection and comparison use only the standard library. There is no installed CLI/package release or deployment yet; consume a reviewed Git revision. Cloud provisioning, Windows, version-level probes and official image-test integration are future work.

## Related projects

[actions/runner-images](https://github.com/actions/runner-images) is the official upstream source for toolsets, provisioning, software reports and runtime tests. [runner-drift](https://github.com/Booyaka101/runner-drift) monitors selected tool-version changes and image migrations. [Mactions](https://github.com/Kyter-com/Mactions) manages ephemeral runners and documents hosted differences. This repository focuses on comparing observations from both machines rather than treating a published software manifest as proof of a candidate's contents.
