# Runner images

- This is public source. Use synthetic examples; keep private candidate inventories, company infrastructure details and credentials out of commits/artifacts.
- Collect from actual machines. Published tool lists are expected configuration, not proof of installed tools.
- `uv run python scripts/check.py` is the static gate. `uv run python scripts/test.py` adds behavioral and branch-coverage checks. Run `actionlint` after workflow changes.
- Public PR workflows use GitHub-hosted runners only. Candidate runs remain in the owning private repositories.
- Generated reference snapshots belong in Actions artifacts, not git. Changes to the JSON schema must preserve consumers or use a new schema version.
