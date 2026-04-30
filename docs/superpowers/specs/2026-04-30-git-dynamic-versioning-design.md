# Git Dynamic Versioning Design

## Goal

Replace the static package version in `pyproject.toml` with Git-derived dynamic versioning so release versions come from manual Git tags and non-tagged commits build as development versions.

## Scope

This change covers:

- Python packaging metadata in `pyproject.toml`
- build backend requirements needed for Git-derived version resolution
- both container build files
- local deploy fingerprint tracking in `setup.sh`
- README and runtime contract tests

This change does not cover:

- automatic Git tag creation
- GitHub Actions release automation
- queue, delivery, or Quadlet runtime behavior unrelated to version resolution

## Approach Options

### Recommended: `setuptools-scm` simplified activation

Keep `setuptools.build_meta` as the build backend, remove the static `project.version`, mark `version` as dynamic, and add `setuptools-scm[simple]` to `build-system.requires`.

Why this is recommended:

- it is the documented setuptools path for Git-derived versioning
- it keeps the current packaging backend intact
- it requires the fewest repo changes
- it naturally produces development versions between release tags

### Alternative: explicit `setuptools-scm` configuration

Use `setuptools-scm` with a `[tool.setuptools_scm]` section and optionally generate a version file.

Tradeoff:

- better if custom tag parsing or a checked-in version file is needed later
- more configuration than this repo currently needs

### Alternative: change build backend

Move to another backend and plugin stack for VCS versioning.

Tradeoff:

- broader packaging migration
- no clear benefit over the simpler setuptools-based approach

## Design

### Version Source Of Truth

Git tags become the only release version source of truth.

The expected release tag format is:

- `v1.2.3`

Behavior:

- if `HEAD` is exactly on `v1.2.3`, the built package version resolves to `1.2.3`
- if `HEAD` is after `v1.2.3`, the built package version resolves to a development version derived from that tag and commit distance
- if the working tree is dirty, local version metadata may reflect that state as part of the derived version

The repository should no longer store a manually edited fixed package version in `pyproject.toml`.

### Packaging Configuration

`pyproject.toml` should:

- remove `project.version`
- add `dynamic = ["version"]` under `[project]`
- keep `setuptools.build_meta`
- add `setuptools-scm[simple]` to `build-system.requires`

This keeps `uv` as the local workflow manager while making version resolution the responsibility of the setuptools build backend during package build and install.

### Local Development Workflow

The supported local workflow remains:

- `uv sync`
- `uv run pytest`

Developers create release versions by manually tagging Git, for example:

```bash
git tag v1.2.3
git push origin v1.2.3
```

No `Makefile` version-bump target is needed in this design, because the package version is no longer edited in the repository.

### Container Build Model

The built images should not contain the repository’s `.git` directory in the final runtime image.

However, Git metadata must be available during the package installation step so `setuptools-scm` can resolve the version from repository state. The preferred contract is:

- make Git metadata available temporarily in the build stage
- do not copy `.git` into the final image layer

The cleanest implementation is to use a build-stage mechanism such as a BuildKit bind mount for `.git` during the install command. This keeps the final image clean while still giving the build backend access to the metadata required for version resolution.

If the container engine cannot support temporary Git metadata exposure during build, the implementation should fail clearly rather than silently producing an incorrect version.

### Deploy Tracking

`setup.sh` currently fingerprints packaging files and source content to decide whether a local rebuild is needed.

With dynamic Git-derived versioning, the build output can change even when `pyproject.toml` and source files do not, for example when:

- a new release tag is added to the same commit
- `HEAD` moves to a new commit without source differences in tracked fingerprint files

The deploy fingerprint therefore needs to include Git version state that affects package version resolution. At minimum, the rebuild decision should become sensitive to the relevant tag and commit identity used by `setuptools-scm`.

The goal is simple: if the resolved package version would change, `setup.sh` should treat that as a rebuild-triggering change.

### Testing And Verification

Tests should lock in the new packaging contract:

- `pyproject.toml` uses dynamic version metadata instead of a static version
- the build system requires `setuptools-scm`
- the README documents manual `vX.Y.Z` tagging as the release flow
- the repo can resolve a non-empty package version from Git metadata in a normal checkout

Container verification should continue to prove both images build successfully with the new packaging model when the local environment supports it.

Verification should include:

- `uv run pytest`
- shell syntax validation for changed shell scripts
- container build validation for `Dockerfile` and `Dockerfile.alpine` when supported by the environment

If container builds cannot be validated in the available environment, that limitation should be stated explicitly at completion.

## Risks And Mitigations

### Missing Git metadata during image build

Risk:
The package install step in the image cannot derive a version if Git metadata is unavailable.

Mitigation:
Expose Git metadata only during the build-stage install step and verify that container builds still succeed under the chosen engine configuration.

### Rebuild drift in local deploy flow

Risk:
`setup.sh` may decide to skip a rebuild even though the resolved package version changed because of new tags or commit movement.

Mitigation:
Extend fingerprint inputs to include the Git-derived version state that affects package resolution.

### Release ambiguity

Risk:
Contributors may continue trying to edit `pyproject.toml` for version bumps out of habit.

Mitigation:
Document clearly that Git tags are the release authority and remove version-bump instructions tied to file edits.

## Success Criteria

- `pyproject.toml` no longer contains a fixed package version
- package versions resolve from Git tags through the build backend
- non-tagged commits produce development versions
- release tags use the `vX.Y.Z` convention
- runtime images build with temporary Git metadata available during package installation
- the final images do not carry `.git` as runtime payload
- `setup.sh` rebuild detection accounts for version-affecting Git state
- tests and README enforce the manual-tag release contract
