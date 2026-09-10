# GHCR Alpine Publish Design

## Goal

Add GitHub Actions workflows that publish the Alpine container image to GitHub Container Registry (`ghcr.io`) when version tags are pushed.

## Scope

This design covers:

- GitHub Actions workflow files under `.github/workflows/`
- tag-triggered publishing to `ghcr.io/<owner>/sms-to-telegram`
- publishing only the Alpine image built from `Dockerfile.alpine`
- release tag handling and image tagging rules
- small README updates describing the release publishing contract

This design does not add publishing for the default non-Alpine image, does not add multi-registry publishing, and does not change the local Quadlet deploy flow.

## Approach Options

### Recommended: Single tag-driven publish workflow

Create one GitHub Actions workflow that runs on pushed version tags, builds `Dockerfile.alpine`, and publishes the image to GHCR.

Why this is recommended:

- directly matches the requested release model
- keeps CI/CD simple and easy to audit
- avoids publishing image variants you do not want in the registry

### Alternative: Release-event-driven workflow

Publish only when a GitHub Release is created, instead of on plain Git tag pushes.

Tradeoff:

- adds a manual or UI-driven release step
- weaker fit for repos that already treat Git tags as the release source of truth

### Alternative: Split validation and publish workflows

Use one workflow for release-tag validation and another reusable workflow for build/publish.

Tradeoff:

- cleaner if the release pipeline is expected to grow
- more structure than the current repo needs

## Design

### Trigger Model

Use a GitHub Actions workflow triggered by pushed tags that match a version pattern such as `v*`.

The workflow should treat Git tags as the release authority. A pushed tag like `v1.2.3` should cause a publish attempt for the Alpine image only.

No publish should happen for branch pushes, pull requests, or non-version tags.

### Registry And Image Naming

Publish to:

- `ghcr.io/<owner>/sms-to-telegram`

The image name stays aligned with the current repository naming.

The workflow should use the repository owner from GitHub context so it publishes under the current namespace without hardcoding a specific username or org in multiple places.

### Image Variants

Only the Alpine image is published.

The workflow should build:

- `Dockerfile.alpine`

It should not publish the default `Dockerfile` image. That image can continue to exist for local development or other uses in the repo, but it is outside this release pipeline.

### Image Tags

For a pushed tag like `v1.2.3`, publish at least:

- `ghcr.io/<owner>/sms-to-telegram:v1.2.3`
- `ghcr.io/<owner>/sms-to-telegram:1.2.3`

This preserves the exact Git tag while also producing a normalized semantic version tag without the leading `v`.

The workflow should not publish `latest` unless the release policy changes later. The current design keeps versioned tags only, because that is the safest and least ambiguous release contract.

### Build And Publish Workflow

The workflow should use standard Docker GitHub Actions:

- `actions/checkout`
- `docker/setup-buildx-action`
- `docker/login-action`
- `docker/build-push-action`

Authentication should use the built-in `GITHUB_TOKEN` with:

- `contents: read`
- `packages: write`

This is sufficient for publishing a package owned by the same repository namespace in standard GHCR setups.

The workflow should:

1. check out the repository
2. derive image tags from the Git ref
3. log in to `ghcr.io`
4. build `Dockerfile.alpine`
5. push the resulting image tags to GHCR

### Metadata And Traceability

The published image should include OCI labels for:

- source repository
- revision SHA
- version tag

This improves traceability from the registry artifact back to the repository and release commit.

If the implementation uses `docker/metadata-action`, that is acceptable, but it is not required. A simpler manual tag/label assembly is also fine if it stays clear and correct.

### README Changes

Update `README.md` to document:

- that GitHub Actions publishes only the Alpine image
- that publishing is triggered by pushed version tags
- that the target registry is `ghcr.io/<owner>/sms-to-telegram`
- the expected tag format, for example `v1.2.3`

The README should describe the release contract clearly without over-documenting workflow internals.

### Verification

Verification should focus on static correctness in the repository:

- YAML should be syntactically valid
- the workflow should reference `Dockerfile.alpine`
- the publish target should be `ghcr.io/<owner>/sms-to-telegram`
- tag extraction should correctly produce both `vX.Y.Z` and `X.Y.Z`
- README should match the implemented workflow behavior

Live publishing cannot be verified locally from this environment, so completion should explicitly note that GitHub Actions execution was not exercised here.

## Risks And Mitigations

### Tag parsing mistakes

Risk:
The workflow could publish malformed tags or fail to normalize the version correctly.

Mitigation:
Keep the tag derivation logic simple and testable, and assert the intended values in repository-level review.

### Registry permission mismatch

Risk:
GHCR publishing may fail if repository/package permissions differ from the standard `GITHUB_TOKEN` model.

Mitigation:
Design for the standard GitHub-owned package path first, and document that unusual org permission models may require follow-up configuration outside the repo.

### Drift between docs and workflow

Risk:
The README could describe a different release contract than the actual workflow implements.

Mitigation:
Add a runtime-contract-style test or assertion pattern that checks key workflow/documentation facts if the repo already uses that style for config contract testing.

## Success Criteria

- the repo contains a GitHub Actions workflow that publishes only `Dockerfile.alpine`
- publishing triggers only on version tag pushes
- images are pushed to `ghcr.io/<owner>/sms-to-telegram`
- release tags publish both `vX.Y.Z` and `X.Y.Z`
- the workflow uses `GITHUB_TOKEN` with appropriate package-write permissions
- the README documents the tag-driven GHCR Alpine publish contract
