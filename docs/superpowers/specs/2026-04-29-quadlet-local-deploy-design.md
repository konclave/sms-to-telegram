# Quadlet Local Deploy Tracking Design

## Context

The current deployment flow relies on a Quadlet unit file and a small `setup.sh` helper that symlinks the unit into `/etc/containers/systemd/` and restarts the service.

That is too thin for the current project shape because the runtime is now assembled from multiple local files:

- container build files
- Python runtime entrypoints
- queue/worker code
- Quadlet configuration

The setup flow needs to answer two operational questions reliably:

- does the local image need to be rebuilt?
- what exact local image state is currently deployed?

## Goals

- Use a locally built image for the Quadlet service instead of a remote registry tag.
- Rebuild only when image-relevant source files change or the local image is missing.
- Track the last successfully built and deployed local image state.
- Keep deploy state local to the repo and ignored by git.
- Keep the service managed from `/etc/containers/systemd/` via Quadlet.

## Non-Goals

- Remote build or registry publishing.
- Multi-host deployment orchestration.
- Persisting deploy state outside the local repo.
- Tracking non-image runtime files as image rebuild inputs unless they actually affect the image.

## Recommended Approach

Use a repo-local deploy state file, ignored by git, for example:

- `.deploy/sms-to-telegram-state.json`

The setup script becomes the local deploy entrypoint. It computes a deterministic fingerprint from the files that affect the container image, checks whether the local image exists, rebuilds only when necessary, installs the Quadlet unit into `/etc/containers/systemd/`, restarts the service, and records the deployed image state.

## Local Image Strategy

The Quadlet unit should point at a local image name instead of a registry image, for example:

- `localhost/sms-to-telegram:latest`

That image name becomes the stable deployment target. The build step refreshes that local tag in place.

The service should never depend on whether `docker.io/kutovoys/sms-to-telegram:latest` happens to be current.

## Deploy State File

Store deploy state in a local file under the repo and add the containing directory to `.gitignore`.

Suggested structure:

```json
{
  "image": "localhost/sms-to-telegram:latest",
  "image_id": "sha256:...",
  "source_fingerprint": "sha256:...",
  "last_built_at": "2026-04-29T18:00:00+00:00",
  "last_deployed_at": "2026-04-29T18:02:00+00:00"
}
```

This state is intentionally local-only. It is an operator aid, not shared project data.

## Rebuild Decision

The rebuild check should not rely on plain git status or modification times.

Instead, compute a fingerprint from the contents of the image-relevant files. Rebuild if either of these is true:

1. the local image tag does not exist
2. the computed fingerprint differs from the stored `source_fingerprint`

This catches:

- committed changes
- uncommitted local edits
- exactly the files that influence the image

## Fingerprint Scope

The fingerprint should include only files that affect the runtime image:

- `Dockerfile`
- `entrypoint.sh`
- `gammurc`
- `sms_forwarder/**`
- `enqueue_sms.py`
- `send_worker.py`
- `check_forwarder_health.py`

Optional:

- `Dockerfile.alpine` only if the setup flow is also responsible for maintaining a local alpine image

It should not include docs, test-only files, or unrelated repo content.

## Setup Script Responsibilities

`setup.sh` should be restructured into a local deploy command with the following sequence:

1. define the stable local image name
2. compute the image source fingerprint
3. read the local deploy state file if it exists
4. check whether the local image exists in Podman
5. build the image only when missing or when the fingerprint changed
6. inspect the resulting image ID
7. install or refresh the Quadlet unit in `/etc/containers/systemd/`
8. reload systemd
9. restart the Quadlet-managed service
10. write the updated deploy state file

The script should print explicit outcomes such as:

- `build skipped: fingerprint unchanged`
- `build triggered: image missing`
- `build triggered: source fingerprint changed`
- `deployed image: sha256:...`

## Quadlet File Changes

The Quadlet file should be simplified around one service definition:

- one local image reference
- one persistent queue volume
- one environment file for secrets

It should not rely on multiple unit variants for live versus local operation.

## Root Access Expectations

The Podman image build and local repo state operations can run without root.

Root access is still required for the system integration steps:

- writing or updating the unit under `/etc/containers/systemd/`
- reloading systemd
- restarting the service

The setup flow should keep privileged operations narrow and predictable.

## Failure Handling

If the image build fails:

- do not update deploy state
- do not restart the service

If systemd reload or service restart fails:

- preserve the successful build metadata if the image was built
- do not write `last_deployed_at`
- report the failure clearly

If the state file is missing or malformed:

- treat it as no previous deploy state
- recompute and continue

## Testing Strategy

### Script-level checks

- first run with no state file builds and deploys
- second run with unchanged fingerprint skips the build
- modifying an image-relevant file triggers a rebuild
- modifying a non-image file does not trigger a rebuild

### Integration checks

- Quadlet unit references the stable local image name
- service restart uses the freshly built image
- deploy state records the new image ID and timestamps

## Migration Scope

Implementation should be limited to:

- restructuring `setup.sh`
- updating the Quadlet unit to a stable local image name
- adding a gitignored local deploy-state directory or file
- documenting the local deploy flow in the README

No broader runtime redesign is needed for this change.
