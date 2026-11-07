# Runner images

- This is public source. Use synthetic examples; keep private candidate inventories, company infrastructure details and credentials out of commits/artifacts.
- Collect from actual machines. Published tool lists are expected configuration, not proof of installed tools.
- `uv run python scripts/check.py` is the static gate. `uv run python scripts/test.py` adds behavioral and branch-coverage checks. Run `actionlint` after workflow changes.
- Public PR workflows use GitHub-hosted runners only. Candidate runs remain in the owning private repositories.
- Hosted reference snapshots are intentionally versioned under `snapshots/github-hosted/`, per the requested Git history contract. Artifacts transfer observations between jobs; Git is the durable reference. Commit only changed observations. Changes to the JSON schema must preserve consumers or use a new schema version.
- Successful public-recipe builds may append immutable bundles under `qualifications/tart/`. Failed comparison or image-identity checks never issue passing records. Physical builder access is restricted to the manual main-branch build workflow; PRs use hosted runners only.
- Rebuild reminders have a stable bot-only marker per profile. All issue mutations belong to the serialized main-only `rebuild-issues.yml` workflow. Close only from independently verified, Git-recorded GHCR publication for the current build-input fingerprint; local qualification/artifact upload alone is insufficient. Keep older uploads as evidence when current inputs change.
