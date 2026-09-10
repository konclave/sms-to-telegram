# Python 3.14 And uv Migration Design

## Goal

Migrate the repository to a proper Python package managed with `uv`, upgrade both container runtime variants to Python `3.14`, and keep the existing local Quadlet deploy flow working with the new packaging model.

## Scope

This migration covers:

- local development and test workflows
- Python packaging and dependency management
- both runtime container images (`Dockerfile` and `Dockerfile.alpine`)
- runtime command wiring used by `entrypoint.sh`, `gammurc`, and the healthcheck
- deploy fingerprint tracking in `setup.sh`
- README and runtime contract tests

This migration does not change the queue semantics, Telegram delivery behavior, or the Quadlet service shape beyond what is required to use the packaged Python runtime.

## Approach Options

### Recommended: Full uv package migration

Convert the repository to a `pyproject.toml`-based package with `sms_forwarder` as the installed Python package, use `uv` as the primary workflow for local development and testing, and install the packaged project into both runtime images.

Why this is recommended:

- one source of truth for Python dependencies
- local and container runtimes use the same installation model
- removes the current loose-script and `PYTHONPATH` wiring
- makes future dependency and entry point changes easier to reason about

### Alternative: Minimal uv migration

Add `pyproject.toml` and use `uv` for dependency management, but keep the current copy-scripts-into-image layout and direct script invocation.

Tradeoff:

- less short-term movement
- keeps the runtime model partially ad hoc

### Alternative: Dev-only uv migration

Use `uv` only for local development while leaving runtime images mostly unchanged.

Tradeoff:

- lowest consistency
- weakest long-term payoff

## Design

### Packaging Model

The repository becomes a Python project with a `pyproject.toml` at the root. The `sms_forwarder` package remains the main code location and becomes the installable distribution. Development dependencies move out of `requirements-dev.txt` and into `pyproject.toml`, with `uv.lock` committed so local development and CI-style verification resolve consistently.

The project should expose console entry points for:

- `sms-forwarder-enqueue`
- `sms-forwarder-worker`
- `sms-forwarder-healthcheck`

These entry points replace the current dependence on loose top-level Python scripts as the primary runtime contract. Thin compatibility wrappers may be kept temporarily if they reduce migration risk, but the container and tests should target the installed commands.

### Local Development Workflow

The documented local workflow becomes:

- `uv sync`
- `uv run pytest`

If the repo currently uses a checked-in or local `.venv`, that environment can remain local-only, but it should be treated as an implementation detail created by `uv`, not as a manually managed virtual environment. The repo should clearly document `uv` as the only supported development workflow after the migration.

### Runtime Image Model

Both `Dockerfile` and `Dockerfile.alpine` are retained.

The Ubuntu-based image should move to a base that supports Python `3.14` cleanly, rather than trying to layer `3.14` onto `ubuntu:focal`. The Alpine image should use an Alpine release that provides Python `3.14`. Both images should install `uv`, copy only the packaging metadata and source tree needed for dependency resolution and installation, and install the project into the image using the packaged layout.

The runtime image should run installed console commands instead of relying on:

- copied helper scripts in `/usr/bin`
- manual `PYTHONPATH=/opt/sms_forwarder`

If a small amount of compatibility copying remains in the short term, it should not be the authoritative runtime path.

### Runtime Wiring

`gammurc`, `entrypoint.sh`, and the container healthcheck should be updated to call the installed commands. The runtime contract becomes “the image contains the `sms-forwarder-*` commands” rather than “the image contains these copied Python files in fixed paths.”

This gives the repo a cleaner boundary:

- package code is installed like a normal Python application
- shell and Quadlet files invoke stable command names
- tests can assert command-based contracts instead of file-path-based ones

### Deploy Tracking

`setup.sh` remains the local deploy entrypoint for Quadlet. Its fingerprint inputs should expand to include the new packaging files that affect the built image:

- `pyproject.toml`
- `uv.lock`
- `Dockerfile`
- `Dockerfile.alpine`
- `entrypoint.sh`
- `gammurc`
- the Python package source tree
- any remaining runtime wrappers, if retained

The state file format in `.deploy/sms-to-telegram-state.json` can remain unchanged. Only the fingerprint inputs need to change so rebuild decisions reflect the new packaging-driven image content accurately.

The canonical locally deployed image remains:

- `localhost/sms-to-telegram:latest`

The deploy flow should still build only that canonical image unless explicitly invoked for the Alpine variant in a separate workflow.

### Testing And Verification

The migration should update tests to verify:

- local tests are runnable via `uv`
- runtime contract assertions target installed command names
- `setup.sh` fingerprint logic includes packaging files that affect image content
- README instructions describe the `uv` workflow and the packaged runtime

Verification should include:

- `uv run pytest`
- shell syntax validation for any modified shell scripts
- container build validation for both Dockerfiles when the environment permits it

If container builds cannot be run in the available environment, that limitation should be stated explicitly when finishing the work.

## Risks And Mitigations

### Base image compatibility

Risk:
Python `3.14` may not be available on the current base image choices.

Mitigation:
Choose base distributions that natively support Python `3.14` instead of forcing unsupported package combinations.

### Runtime command breakage

Risk:
Shell config and service files may still point at old script paths.

Mitigation:
Add runtime contract tests that assert the command references used by `gammurc`, `entrypoint.sh`, and container healthchecks.

### Drift between local and container installs

Risk:
Local `uv` setup and container installation may diverge.

Mitigation:
Use the same packaged project metadata and installation model in both places, and avoid dual dependency declarations.

## Success Criteria

- the repository has a `pyproject.toml`-based Python package layout
- `uv` is the documented and tested local workflow
- both runtime images run on Python `3.14`
- runtime wiring uses installed commands instead of loose script paths
- `setup.sh` rebuild tracking includes the new packaging inputs
- tests and README enforce the new workflow and runtime contract
