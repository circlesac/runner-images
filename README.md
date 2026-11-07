# Runner images

Inspect and compare GitHub Actions runner images on local machines and cloud VMs. Collect hosted references, build local Apple Silicon Tart images, and compare actual capabilities on Linux and macOS. GCP/Azure provisioning and complete version/runtime equivalence remain outside the initial scope.

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

The [Hosted reference inventories](https://github.com/circlesac/runner-images/actions/workflows/reference.yml) workflow captures standard `ubuntu-24.04` and `macos-26` images before setup actions change their toolchain. It runs daily at **09:30 KST (00:30 UTC)**, on pushes to main, on pull requests, and on manual dispatch.

**Git is the durable reference:** [`snapshots/github-hosted/`](snapshots/github-hosted/) contains the current measured JSON for each image. Changed commands, caches, environment names or image versions produce a commit; identical observations produce no commit. Git history preserves previous observations and readable diffs. Observation timestamps and run counters are not inserted into snapshots, so reruns do not create artificial changes. Initial snapshots were captured by [the verified hosted run](https://github.com/circlesac/runner-images/actions/runs/42367660196).

Artifacts only transfer the two matrix jobs’ results to the Git recording job. That job validates the entire matrix, updates only the snapshot directory, and commits with the workflow token. Public PRs cannot write snapshots; only trusted main runs can. Publishing is serialized, starts from current main, and never force-pushes over a concurrent change. A failed/incomplete observation preserves the previous references. Scheduled recording starts once this workflow is merged to the default branch.

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

Replace `VERIFIED_COMMIT` with the reviewed code revision. Consumers fetch the public repository’s main branch, read `snapshots/github-hosted/macos-26.json` (or `ubuntu-24.04.json`), and retain that reference commit with their comparison evidence. Reading versioned references needs no Actions-artifact permission or expiring artifact URL. During initial adoption before the public PR is merged, the same verified PR commit also contains the seeded snapshots.

Candidate machines run in their owning private workflows. This repository does not accept public PR code on a private self-hosted runner and never uploads private candidate inventories. Reference collection uses standard GitHub-hosted public-repository runners; cloud VM costs and larger-runner billing are separate.

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

## Build → compare → upload → record in GitHub Actions

[Build and qualify Tart image](.github/workflows/build-macos.yml) is a manual **main-only** workflow. Select `xcode` or `xcode-android`; it builds a uniquely named image, runs native checks and the reviewed comparison, and binds the image files to the recipe/reference Git commits and upstream toolset. After the local image hash recheck, `scripts/publish-candidate.py` uploads that exact candidate to GHCR, reads back the manifest/config and verifies labels binding it to the qualification receipt and build inputs. The hosted recording job independently repeats the registry check before appending evidence to Git.

The recording job runs on GitHub-hosted Ubuntu. It independently validates input hashes against the dispatched source, repeats the capability comparison, and commits an immutable bundle under `qualifications/tart/PROFILE/`. Each bundle has a readable summary, receipt, measured inventory, exact toolset, native check output and, for uploaded candidates, `publication.json` with the immutable OCI digest and build-input fingerprint. Failed builds only retain diagnostic artifacts. Rerunning the record job preserves the same record and remote history.

The build job needs a **physical Apple Silicon Mac** with Tart, jq, curl and Python 3.11+. GitHub-hosted macOS and the ordinary macOS VM fleet cannot run nested macOS VMs ([GitHub limitation](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)). It targets a dedicated `runner-image-builders` group using standard `self-hosted`, `macOS`, `ARM64` labels. Before registering a physical runner, restrict that group to this repository and only `circlesac/runner-images/.github/workflows/build-macos.yml@refs/heads/main`; keep the existing Default group unchanged. The dedicated physical runner/group is **not registered yet**. Public PRs continue to use hosted runners exclusively.

### Rebuild reminders and completion

[Reconcile image rebuild issues](.github/workflows/rebuild-issues.yml) runs on main changes, after successful trusted reference/build workflows, daily at **09:45 KST**, or manually. It always reads current main. `workflow_run` is intentional: commits made by the workflow token do not trigger ordinary push workflows. PR source and artifacts never drive this privileged job.

There is **one reusable bot-owned issue per profile** (`xcode`, `xcode-android`). The initial missing upload creates a reminder. A changed macOS reference, reviewed profile policy or build code updates/reopens the same issue. Documentation, Linux observations and generated evidence do not request a rebuild. A single Actions concurrency queue serializes all issue writers; the reconciler paginates the actual issues API, matches a stable profile marker and repairs preexisting duplicate bot issues. An unchanged run neither edits the issue nor adds comments. Unrelated issues, including the implementation issue, are preserved.

Dispatch the linked main-branch build workflow for the requested profile. Build, native checks, comparison, byte-identity checks, GHCR upload, registry read-back and Git evidence recording must all succeed before the reminder closes. A local passing image or JSON artifact upload alone cannot close it. If the reference/recipe changed during the build, the older uploaded candidate remains recorded but the current reminder stays open. Replaying completion is harmless; later changes reopen that same issue.

Candidate images use `ghcr.io/circlesac/runner-images/macos-26-PROFILE:macos-26-PROFILE-RUNID-ATTEMPT`; consumers should use the immutable `@sha256:…` reference in `publication.json`. Upload and read-back use only the job-scoped GitHub token (`packages: write` on the trusted builder, `packages: read` on the recorder), never persistent registry login credentials. Locally, the same publisher accepts `TART_REGISTRY_USERNAME`/`TART_REGISTRY_PASSWORD`; the GitHub evidence reader also accepts `GH_TOKEN`.

These are verification candidates, not a public distribution release or fleet rollout. Package visibility is not changed by the workflow; newly created GHCR packages may remain private. Public availability, CalVer releases with SBOM/provenance and GitHub Release publication remain separate future release work. There are no real candidate upload records yet; physical-builder activation and the first live upload remain pending.

## Development and verification

```sh
uv sync --frozen
uv run python scripts/check.py
uv run python scripts/test.py
uv run ruff format .
shfmt -w scripts macos
```

Install ShellCheck and shfmt with your system package manager. The gate runs formatting, Python/shell lint, strict Python types and behavioral tests with branch coverage (minimum 80%). Tests exercise real executable files/symlinks, cache completion, secret-value omission, incompatible platforms, invalid snapshots and stale allowlists; Git recording is exercised against a real local bare remote, including repeat no-op and dirty-worktree refusal. Tart lifecycle tests cover successful publication, provisioning/verification/rename failures, cleanup and preservation of unrelated images through a controlled command boundary. A local real-VM build is a separate integration check, not established by those fixtures.

| Tooling | Version at selection (2026-11-07) | Registry release | Reason |
| --- | --- | --- | --- |
| Ruff | 0.17.4 | 2026-11-05 | Native formatter and linter; selected over formatter-only Black 26.10.1 (2026-10-10) |
| pytest / pytest-cov | 9.2.0 / 7.1.0 | 2026-10-27 / 2026-03-21 | Behavioral tests and branch coverage |
| Pyright | 1.1.414 | 2026-09-10 | Strict typed collection/comparison boundary |
| shfmt / ShellCheck | 3.14.1 / 0.11.0 locally | Distribution packages in CI | Maintained native shell formatter/linter; avoids an interpreter-based formatter or bashate lint pipeline |

`setup-uv` uses its current full release ref because upstream does not publish a `v10` alias. Development dependencies use compatible ranges and `uv.lock`; runtime collection/comparison uses only the standard library. There is no installed CLI/package release or fleet deployment; consume a reviewed Git revision. Cloud provisioning, Windows, version-level probes and official upstream test integration are future work.

## Related projects

[actions/runner-images](https://github.com/actions/runner-images) is the official upstream source for toolsets, provisioning, software reports and runtime tests. [runner-drift](https://github.com/Booyaka101/runner-drift) monitors selected tool-version changes and migrations. [Mactions](https://github.com/Kyter-com/Mactions) manages ephemeral runners and documents hosted differences. This repository compares observations from both machines rather than treating a published manifest as proof of a candidate’s contents.
