# macOS runner images

Build and qualify macOS GitHub Actions runner images on Apple Silicon Macs. Collect the GitHub-hosted `macos-26` reference, build local Tart images, compare the installed capabilities, and publish verified images with their evidence.

## Supported scope

- Images: macOS 26 on ARM64, with `xcode` and `xcode-android` profiles.
- Reference snapshots: the actual GitHub-hosted `macos-26` image.
- Building: a local Apple Silicon Mac using Tart. GitHub Actions validates the evidence and publishes the release.
- CI: macOS and Ubuntu run the Python/shell checks; Ubuntu also records snapshots and publishes macOS release evidence. These jobs do not build or release Linux images.

The generic inventory schema still accepts Linux observations for compatibility. The image recipes, reference collection, rebuild reminders and releases in this repository are macOS-only.

## What is measured

- Executable names reachable on `PATH`.
- Tool-cache families with a setup-action `.complete` marker.
- Image environment variable names, excluding runner/job/session metadata. Environment values, home directories, hostnames and full executable paths are not recorded.
- OS, architecture and optional `ImageOS`/`ImageVersion` provenance. Only equal OS/architecture pairs can be compared.

Comparison fails for unexpected missing capabilities and stale allowlist entries. Extra capabilities are reported without failing. An executable name proves discoverability; product tests and version checks still belong to the image consumer.

## Run locally

Python 3.11 or newer is the only runtime dependency. Run the same collector on each Mac; it needs neither service credentials nor root access.

```sh
python3 scripts/inventory.py > candidate.json
python3 scripts/compare.py reference.json candidate.json
python3 scripts/compare.py reference.json candidate.json --allowlist accepted-missing.txt
```

Output is JSON. Comparison exits 0 when compatible, 1 for capability differences, and 2 for invalid/unreadable input or mismatched platforms. The allowlist is a reviewed text file, one record per line, such as `cmd example-tool` or `cache ExampleRuntime`. Missing and stale exceptions are checked in both directions. Keep candidate inventories and company-specific exception policies in their owning repositories.

## GitHub Actions

The [Hosted reference inventories](https://github.com/circlesac/runner-images/actions/workflows/reference.yml) workflow captures the standard `macos-26` image before setup actions change its toolchain. It runs daily at **09:30 KST (00:30 UTC)**, on pushes to main, on pull requests, and on manual dispatch.

**Git is the durable reference:** [`snapshots/github-hosted/`](snapshots/github-hosted/) contains the current measured JSON for that image. Changed commands, caches, environment names or image versions produce a commit; identical observations produce no commit. Git history preserves previous observations and readable diffs. Observation timestamps and run counters are not inserted into snapshots, so reruns do not create artificial changes. Initial snapshots were captured by [the verified hosted run](https://github.com/circlesac/runner-images/actions/runs/42367660196).

Artifacts transfer the macOS observation to the Git recording job on Ubuntu. That job validates the observation, updates only the snapshot directory, and commits with the workflow token. Public PRs cannot write snapshots; only trusted main runs can. Publishing is serialized, starts from current main, and never force-pushes over a concurrent change. A failed/incomplete observation preserves the previous references.

The root composite Action collects a candidate:

```yaml
- uses: circlesac/runner-images@VERIFIED_COMMIT
  with:
    output: candidate.json
```

Check out the factory reference at the same reviewed revision before comparing:

```yaml
- uses: actions/checkout@v7
  with:
    repository: circlesac/runner-images
    ref: VERIFIED_COMMIT
    path: .runner-images
- uses: circlesac/runner-images/compare@VERIFIED_COMMIT
  with:
    reference: .runner-images/snapshots/github-hosted/macos-26.json
    candidate: candidate.json
    allowlist: accepted-missing.txt
```

Replace `VERIFIED_COMMIT` with the reviewed code revision. Consumers fetch the public repository’s main branch, read `snapshots/github-hosted/macos-26.json`, and retain that reference commit with their comparison evidence. Reading versioned references needs no Actions-artifact permission or expiring artifact URL.

Candidate machines run in their owning private workflows. This repository does not accept public PR code on a private self-hosted runner and never uploads private candidate inventories. Reference collection uses standard GitHub-hosted public-repository runners.

## Build a Tart image

On an Apple Silicon Mac with Tart, curl, jq and Python 3.11+, run:

```sh
bash macos/build-image.sh example-runner
bash macos/build-image.sh example-android ghcr.io/cirruslabs/macos-tahoe-xcode:latest xcode-android
```

The arguments are destination image, base image (a local Tart image or OCI reference), profile (`xcode` by default, or `xcode-android`), and guest runner directory (`/Users/admin/actions-runner` by default). The Cirrus base supplies macOS and Xcode. The scripts provision GitHub’s macOS toolset, Ruby gems, Rust components, Python/Node/Go caches and Temurin JDKs; the Android profile adds SDK command-line tools, platform-tools and the toolset’s minimum platform/build-tools packages. This is a concrete supported tool subset, not an assertion that every hosted software family is installed.

Build from a clean committed checkout so every verification is tied to an exact source revision. The optional fifth/sixth arguments select a reference JSON and reviewed missing-capability list; defaults are the versioned macOS reference and `macos/accepted-missing/PROFILE.txt`. Those lists explicitly describe the supported profiles’ intentional omissions.

The builder selects an active physical bridge even when a VPN owns the default route. It refuses an existing destination and a fully occupied two-VM host, builds an isolated temporary clone, installs the checksum-verified GitHub runner without registration credentials, checks the job toolchain, captures the native check output, resolved toolset and inventory, stops the clone, compares against the reference and hashes the base/final VM files. It publishes under the new local name only after comparison passes, rechecks the renamed image’s hashes and then issues `artifacts/tart/IMAGE/qualification.json`. Failed comparison or identity checks issue no passing receipt. Failure stops/removes only its temporary clone. Production fleet images and runner configuration are not switched by this script. Keep private image identifiers, credentials and company configuration with the fleet owner.

Guest scripts live in [`macos/image/`](macos/image/). Reuse a pinned base digest when an exact base is required; the resolved toolset is retained inside the guest under `~/.runner-image/`. Xcode comes from the selected base image; this script does not redistribute a host Xcode installation or export simulator runtimes.

## One command from the rebuild issue

The daily reference job records real hosted-image changes at 09:30 KST. [Reconcile image rebuild issues](.github/workflows/rebuild-issues.yml) compares the current macOS reference/build inputs with released images. It maintains **one reusable bot-owned issue per profile** (`xcode`, `xcode-android`), including a command already filled in for that profile. Copy that command into a terminal on your Apple Silicon Mac; there are no workflow inputs, image names, version numbers, tokens or follow-up release commands to enter.

The command downloads the public `macos/rebuild.sh` bootstrap through your existing `gh` login. It installs missing Tart/jq/Grype/Python tools through Homebrew, prepares its own checkout under `${XDG_STATE_HOME:-~/.local/state}/runner-images/circlesac/PROFILE/source`, and runs the repository's local build command. Your ordinary working tree and running fleet configuration are untouched. `gh`, an authenticated GitHub account with repository/workflow/package write access, and Homebrew are prerequisites. These are the account's existing permissions; no physical Actions runner registration or new runner group is required.

The local command waits for a free VM slot, builds a uniquely named image, generates a Syft guest SBOM, checks the native toolchain and capability comparison, stops the VM and checks its bytes. Grype rejects high/critical findings in the measured SBOM. It then uploads that exact image to GHCR, reads back its immutable digest and qualification labels, commits verified evidence to main using the existing GitHub login, and starts the main-only [Release verified Tart image](.github/workflows/release-tart.yml) workflow. The terminal waits for that workflow to finish; you do not have to visit Actions or manually close the issue.

GitHub independently validates the evidence against its original source revision on main, repeats the comparison and GHCR read-back, scans the SBOM again, and signs the SBOM and local-build validation evidence. `oneup` computes the next CalVer version with a separate `tart-PROFILE@YY.MM.MICRO` tag sequence. The workflow uploads the evidence as release assets, verifies their SHA256 digests, publishes the GitHub Release and records `release.json` in Git. A draft is only an intermediate upload stage; a completed run leaves a published release. Interrupted publication repairs that same release instead of creating another version. The signed custom predicate explicitly identifies the builder as a **local Mac** and attests GitHub's independent validation; it does not claim that GitHub built the VM.

Only then does the serialized issue reconciler close the matching reminder. An uploaded candidate without a completed release cannot close an issue. If the reference or recipe changed while the build ran, the older release is preserved and the current request stays open. Repeated detection makes no comments or duplicate issues. Later changes reopen the same issue; unrelated issues are preserved. The reconciler reads current main after trusted reference/release runs, on main pushes and daily at 09:45 KST. The completion workflow handles workflow-token commits explicitly rather than relying on push events that GitHub suppresses.

The local journal lives in the isolated checkout's ignored `artifacts/` directory. Run the **same issue command** after a failure or interruption: verified images/uploads are reused, failed generated records are preserved under `artifacts/recovery/`, and an existing release run is reconnected or rerun. A second concurrent local invocation cannot build another image for that profile. Repeating a completed command when current inputs are already released is a no-op. Failed builds retain diagnostics under their own unique names and preserve prior images.

## Image and release evidence

Git is the durable store: `qualifications/tart/PROFILE/RECEIPT_SHA256/` contains the qualification receipt, inventory, upstream toolset, native check output, guest SBOM, registry `publication.json` and completed `release.json`. Source/reference revisions, input hashes and stopped VM file hashes bind that record to the actual qualified candidate. Release assets add the vulnerability report and signed validation/SBOM bundles. Fixtures are never published as real build evidence.

The VM itself is stored as `ghcr.io/circlesac/runner-images/macos-26-PROFILE:macos-26-PROFILE-local-BUILD_ID`. Use the immutable `@sha256:…` reference shown in the release notes to clone it with Tart. GitHub Releases contain the version, pull command and verification evidence, rather than another multi-gigabyte copy of the VM. Package visibility follows GHCR permissions and is not changed automatically; a public release page does not imply anonymous registry access. Fleet adoption remains the consumer's decision.

The SBOM describes software discoverable by Syft in the fresh guest, excluding protected/system, volatile and user Library trees. Native product tests, complete hosted-image equivalence, proprietary installer licensing and public package-visibility changes are not established by a capability comparison or SBOM. The first real build, upload and release through this factory remains to be verified; fixture tests do not establish that end-to-end result. Public PR checks use hosted runners and controlled external-boundary fixtures.

## Development and verification

```sh
uv sync --frozen
uv run python scripts/check.py
uv run python scripts/test.py
uv run ruff format .
shfmt -w scripts macos
```

Install ShellCheck and shfmt with your system package manager. The gate runs formatting, Python/shell lint, strict Python types and behavioral tests with branch coverage (minimum 80%). Tests exercise real executable files/symlinks, cache completion, secret-value omission, incompatible platforms, invalid snapshots and stale allowlists; Git recording is exercised against a real local bare remote, including repeat no-op and dirty-worktree refusal. Tart lifecycle tests cover successful publication, provisioning/verification/rename failures, cleanup and preservation of unrelated images through a controlled command boundary. A local real-VM build is a separate integration check, not established by those fixtures.

The `mac-sbom` job in [Check](.github/workflows/check.yml) runs `scripts/check-sbom.py` against a real Syft binary so guest exclusion rules stay aligned with the collector script.

| Tooling | Version at selection (2026-11-07) | Registry release | Reason |
| --- | --- | --- | --- |
| Ruff | 0.17.4 | 2026-11-05 | Native formatter and linter; selected over formatter-only Black 26.10.1 (2026-10-10) |
| pytest / pytest-cov | 9.2.0 / 7.1.0 | 2026-10-27 / 2026-03-21 | Behavioral tests and branch coverage |
| Pyright | 1.1.414 | 2026-09-10 | Strict typed collection/comparison boundary |
| shfmt / ShellCheck | 3.14.1 / 0.11.0 locally | Distribution packages in CI | Maintained native shell formatter/linter; avoids an interpreter-based formatter or bashate lint pipeline |

`setup-uv` uses its current full release ref because upstream does not publish a `v10` alias. Development dependencies use compatible ranges and `uv.lock`; runtime collection/comparison uses only the standard library. There is no installed CLI/package release or fleet deployment; consume a reviewed Git revision. A complete build/upload/release/adoption run, version-level probes and official upstream test integration require separate integration verification.

## Related projects

[actions/runner-images](https://github.com/actions/runner-images) is the official upstream source for toolsets, provisioning, software reports and runtime tests. [runner-drift](https://github.com/Booyaka101/runner-drift) monitors selected tool-version changes and migrations. [Mactions](https://github.com/Kyter-com/Mactions) manages ephemeral runners and documents hosted differences. This repository compares observations from actual machines rather than treating a published manifest as proof of a candidate’s contents.

### Local consumers after macOS publication

The issue command can notify a host-local image consumer after it reads a published release receipt from `origin/main`. Register a JSON command array at `${XDG_CONFIG_HOME:-~/.config}/runner-images/circlesac/<profile>/after-release.json`, for example `["/absolute/path/to/example-consumer"]`. The command receives the absolute qualification-bundle directory as its last argument. Arguments are passed directly, without a shell. No consumer configuration or credentials are committed here.

This callback also runs for an already published matching image, so registering later or repeating a failed adoption does not require another build. A callback failure fails the local command and leaves its journal unfinished. The public rebuild issue tracks image publication and closes after verified release publication; private consumer adoption is recorded by that consumer. Without a registered callback the command only publishes the image and never changes a fleet.
