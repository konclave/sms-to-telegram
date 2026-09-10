# Git Dynamic Versioning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the static package version with Git-derived dynamic versioning, keep container builds working with temporary Git metadata during install, and make local deploy rebuild detection version-aware.

**Architecture:** Keep `setuptools.build_meta` as the build backend and enable Git-derived versions through `setuptools-scm`. Container images should resolve the package version during a builder-stage install where `.git` metadata and `git` are temporarily available, then copy only the built environment into the runtime image. `setup.sh` should fold Git version state into its fingerprint so tag and commit changes that alter the resolved package version trigger rebuilds.

**Tech Stack:** Python 3.14, uv, setuptools, setuptools-scm, pytest, Docker/Podman, shell scripts

---

## Preflight

The repository currently has no version tags. Before relying on this packaging model for published builds, create an initial baseline tag on the commit you want to represent the current release, for example:

```bash
git tag v0.1.0
git push origin v0.1.0
```

Do not block the code changes on this step. Tests in this plan should create synthetic tags in temporary repo copies instead of mutating the real repo tags.

## File Structure

### Existing files to modify

- `pyproject.toml`
  - Replace the fixed `project.version` with dynamic version metadata and add `setuptools-scm` to the build requirements.
- `uv.lock`
  - Re-lock after the packaging metadata change so the lockfile stays aligned with the project definition.
- `Dockerfile`
  - Convert the app installation path to a builder-stage flow that has temporary Git metadata and installs the project separately from dependency sync.
- `Dockerfile.alpine`
  - Apply the same Git-aware builder-stage install model to the Alpine image.
- `setup.sh`
  - Add Git version state to the fingerprint input stream so version-affecting tag or commit changes trigger a rebuild.
- `README.md`
  - Document manual `vX.Y.Z` tagging, development versions between tags, and the rebuild implications of Git-derived version state.
- `tests/test_runtime_contract.py`
  - Assert the dynamic packaging metadata and Git-aware container build contract.
- `tests/test_setup_script.py`
  - Assert that Git tag and commit changes alter the setup fingerprint.

### New files to create

- `docs/superpowers/plans/2026-04-30-git-dynamic-versioning.md`
  - This implementation plan.

## Task 1: Switch packaging metadata to Git-derived versions

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `tests/test_runtime_contract.py`
- Test: `tests/test_runtime_contract.py`

- [ ] **Step 1: Write the failing packaging contract tests**

Update `tests/test_runtime_contract.py` so the packaging contract is dynamic and Git-based.

```python
import os
import re
import zipfile


def test_repo_uses_git_dynamic_versioning_metadata():
    pyproject = tomllib.loads(Path("pyproject.toml").read_text())

    assert pyproject["build-system"] == {
        "requires": ["setuptools>=80", "setuptools-scm[simple]>=9.2"],
        "build-backend": "setuptools.build_meta",
    }
    assert pyproject["project"]["dynamic"] == ["version"]
    assert "version" not in pyproject["project"]
    assert pyproject["project"]["requires-python"] == ">=3.14,<3.15"
    assert Path("uv.lock").exists()


def test_build_backend_derives_a_development_version_from_git_history(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(
        Path.cwd(),
        repo,
        ignore=shutil.ignore_patterns(".venv", ".pytest_cache", ".uv-cache", "__pycache__"),
    )
    subprocess.run(["git", "tag", "v0.1.0"], cwd=repo, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test User",
            "-c",
            "user.email=test@example.com",
            "commit",
            "--allow-empty",
            "-m",
            "after tag",
        ],
        cwd=repo,
        check=True,
    )

    dist = tmp_path / "dist"
    env = os.environ | {"UV_CACHE_DIR": str(repo / ".uv-cache")}
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(dist)],
        cwd=repo,
        env=env,
        check=True,
        text=True,
        capture_output=True,
    )

    wheel = next(dist.glob("sms_to_telegram-*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        metadata_name = next(name for name in archive.namelist() if name.endswith("METADATA"))
        metadata = archive.read(metadata_name).decode()

    version_line = next(line for line in metadata.splitlines() if line.startswith("Version: "))
    resolved = version_line.removeprefix("Version: ")
    assert re.match(r"^\d+\.\d+\.\d+", resolved)
    assert ".dev" in resolved
```

- [ ] **Step 2: Run the targeted tests to verify they fail**

Run: `UV_CACHE_DIR=.uv-cache uv run pytest tests/test_runtime_contract.py::test_repo_uses_git_dynamic_versioning_metadata tests/test_runtime_contract.py::test_build_backend_derives_a_development_version_from_git_history -v`
Expected: FAIL because `pyproject.toml` still declares `version = "0.1.0"` and the build backend does not yet know how to derive versions from Git tags.

- [ ] **Step 3: Update the packaging metadata**

Change `pyproject.toml` to remove the fixed version and enable `setuptools-scm` simplified activation.

```toml
[build-system]
requires = ["setuptools>=80", "setuptools-scm[simple]>=9.2"]
build-backend = "setuptools.build_meta"

[project]
name = "sms-to-telegram"
dynamic = ["version"]
description = "Forward incoming SMS messages to Telegram with a durable local queue."
requires-python = ">=3.14,<3.15"
dependencies = []

[project.optional-dependencies]
dev = ["pytest==8.4.1"]

[project.scripts]
sms-forwarder-enqueue = "sms_forwarder.enqueue_hook:main"
sms-forwarder-worker = "sms_forwarder.worker:main"
sms-forwarder-healthcheck = "sms_forwarder.healthcheck:main"

[dependency-groups]
dev = ["pytest==8.4.1"]

[tool.setuptools]
packages = ["sms_forwarder"]
```

Do not add a fallback static version. The Git tag history is the intended version source of truth.

- [ ] **Step 4: Refresh the lockfile**

Run: `UV_CACHE_DIR=.uv-cache uv lock`
Expected: `uv.lock` is updated to reflect the new project metadata without introducing unrelated dependency changes.

- [ ] **Step 5: Run the targeted tests to verify they pass**

Run: `UV_CACHE_DIR=.uv-cache uv run pytest tests/test_runtime_contract.py::test_repo_uses_git_dynamic_versioning_metadata tests/test_runtime_contract.py::test_build_backend_derives_a_development_version_from_git_history -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock tests/test_runtime_contract.py
git commit -m "feat: derive package version from git tags"
```

## Task 2: Make local deploy fingerprints sensitive to Git version state

**Files:**
- Modify: `setup.sh`
- Modify: `tests/test_setup_script.py`
- Test: `tests/test_setup_script.py`

- [ ] **Step 1: Write the failing fingerprint tests**

Extend `tests/test_setup_script.py` with a Git-aware fingerprint contract.

```python
def test_setup_fingerprint_changes_when_git_version_state_changes(tmp_path):
    repo_root = Path.cwd()
    repo = prepare_repo_copy(tmp_path, repo_root)

    env = os.environ | {
        "PATH": os.environ["PATH"],
        "STATE_DIR": str(repo / ".deploy"),
    }

    def fingerprint() -> str:
        return subprocess.run(
            ["bash", "setup.sh", "--print-fingerprint"],
            cwd=repo,
            env=env,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    initial = fingerprint()
    subprocess.run(["git", "tag", "v0.1.0"], cwd=repo, check=True)
    after_tag = fingerprint()
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test User",
            "-c",
            "user.email=test@example.com",
            "commit",
            "--allow-empty",
            "-m",
            "advance version state",
        ],
        cwd=repo,
        check=True,
    )
    after_commit = fingerprint()

    assert after_tag != initial
    assert after_commit != after_tag
```

Update the existing docs-change fingerprint test so it no longer assumes README-only edits are invisible. With Git-derived versions, a dirty working tree can affect the resolved package version, so that test should focus on tracked packaging and runtime inputs plus explicit Git tag changes rather than “README never matters.”

- [ ] **Step 2: Run the targeted tests to verify they fail**

Run: `UV_CACHE_DIR=.uv-cache uv run pytest tests/test_setup_script.py::test_setup_fingerprint_changes_when_git_version_state_changes -v`
Expected: FAIL because `setup.sh --print-fingerprint` currently ignores Git tag and commit state.

- [ ] **Step 3: Add a Git version state helper to `setup.sh`**

Add a helper that emits the Git state relevant to `setuptools-scm`, then include it in `compute_source_fingerprint`.

```bash
emit_git_version_state() {
  (
    cd "$REPO_ROOT"
    if ! git rev-parse --git-dir >/dev/null 2>&1; then
      printf '%s\0' "git-unavailable"
      return
    fi

    printf '%s\0' ".git/HEAD"
    git rev-parse HEAD

    printf '%s\0' ".git/describe"
    if git describe --dirty --tags --long --always --match 'v[0-9]*' >/dev/null 2>&1; then
      git describe --dirty --tags --long --always --match 'v[0-9]*'
    else
      git rev-parse --short HEAD
    fi
  )
}
```

Fold that helper into the fingerprint stream:

```bash
    {
      for path in "${IMAGE_INPUTS[@]}"; do
        printf '%s\0' "$path"
        cat "$path"
      done
      find sms_forwarder -type f | sort | while read -r path; do
        printf '%s\0' "$path"
        cat "$path"
      done
      emit_git_version_state
    } | python3 -c 'import hashlib, sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())'
```

- [ ] **Step 4: Run the setup script tests**

Run: `UV_CACHE_DIR=.uv-cache uv run pytest tests/test_setup_script.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add setup.sh tests/test_setup_script.py
git commit -m "feat: track git version state in deploy fingerprint"
```

## Task 3: Rework container installs so version resolution uses temporary Git metadata

**Files:**
- Modify: `Dockerfile`
- Modify: `Dockerfile.alpine`
- Modify: `tests/test_runtime_contract.py`
- Test: `tests/test_runtime_contract.py`

- [ ] **Step 1: Write the failing container contract tests**

Extend `tests/test_runtime_contract.py` so both Dockerfiles are required to install the app with temporary `.git` access and produce runtime images that do not ship `git`.

```python
def test_container_files_resolve_version_with_temporary_git_metadata():
    primary = Path("Dockerfile").read_text()
    alpine = Path("Dockerfile.alpine").read_text()

    assert "--mount=type=bind,source=.git,target=/app/.git" in primary
    assert "--mount=type=bind,source=.git,target=/app/.git" in alpine
    assert "uv sync --frozen --no-dev --no-install-project" in primary
    assert "uv sync --frozen --no-dev --no-install-project" in alpine
    assert "uv pip install --python /app/.venv/bin/python --no-deps --no-build-isolation ." in primary
    assert "uv pip install --python /app/.venv/bin/python --no-deps --no-build-isolation ." in alpine
```

Update the runtime image check so it proves the installed package exposes a resolved version and the final image does not contain the `git` executable.

```python
        result = _run(
            engine,
            "run",
            "--rm",
            "--entrypoint",
            "sh",
            tag,
            "-c",
            (
                "python3 --version && "
                "uv --version && "
                "command -v sms-forwarder-enqueue && "
                "command -v sms-forwarder-worker && "
                "command -v sms-forwarder-healthcheck && "
                "python3 -c \"from importlib.metadata import version; print(version('sms-to-telegram'))\" && "
                "! command -v git"
            ),
        )
```

- [ ] **Step 2: Run the targeted tests to verify they fail**

Run: `UV_CACHE_DIR=.uv-cache uv run pytest tests/test_runtime_contract.py::test_container_files_resolve_version_with_temporary_git_metadata tests/test_runtime_contract.py::test_container_images_expose_python_uv_and_packaged_console_scripts -v`
Expected: FAIL because the current Dockerfiles install the project in a single stage without Git metadata, and the runtime assertions do not yet check the resolved version or the absence of `git`.

- [ ] **Step 3: Convert `Dockerfile` to a builder-stage app install**

Use a dedicated app-builder stage that has `git` available, mounts `.git` only for the install step, and copies only the built virtual environment into the final image.

```dockerfile
# syntax=docker/dockerfile:1.7
FROM python:3.14-slim-bookworm AS app-builder

ENV UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:${PATH}"

RUN apt-get update && \
    apt-get install --no-install-recommends -y git ca-certificates && \
    python -m pip install --no-cache-dir "setuptools>=80" uv && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
COPY sms_forwarder ./sms_forwarder
RUN --mount=type=bind,source=.git,target=/app/.git \
    uv sync --frozen --no-dev --no-install-project && \
    uv pip install --python /app/.venv/bin/python --no-deps --no-build-isolation .

FROM python:3.14-slim-bookworm
ENV PIN=0000 \
    LC_ALL=en_US.UTF-8 \
    LANG=en_US.UTF-8 \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:${PATH}"

RUN apt-get update && \
    apt-get install --no-install-recommends -y \
        gammu-smsd \
        gettext \
        locales \
        ca-certificates && \
    localedef -i en_US -c -f UTF-8 -A /usr/share/locale/locale.alias en_US.UTF-8 && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=app-builder /app/.venv /app/.venv
COPY gammurc /etc/gammurc
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
```

Keep the existing queue directories, healthcheck, and entrypoint wiring intact in the final stage.

- [ ] **Step 4: Convert `Dockerfile.alpine` to the same install contract**

Keep the existing Gammu builder stage, add a dedicated Python app-builder stage with `git`, then copy the built virtual environment into the final runtime stage.

```dockerfile
# syntax=docker/dockerfile:1.7
FROM python:3.14-alpine3.23 AS app-builder

ENV UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:${PATH}"

RUN apk add --no-cache git ca-certificates && \
    python -m pip install --no-cache-dir "setuptools>=80" uv

WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
COPY sms_forwarder ./sms_forwarder
RUN --mount=type=bind,source=.git,target=/app/.git \
    uv sync --frozen --no-dev --no-install-project && \
    uv pip install --python /app/.venv/bin/python --no-deps --no-build-isolation .

FROM python:3.14-alpine3.23
ENV PIN=0000 \
    TZ=UTC \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:${PATH}"

RUN apk add --no-cache \
    libusb \
    libcurl \
    libgcc \
    libstdc++ \
    tzdata \
    gettext \
    ca-certificates && \
    python -m pip install --no-cache-dir uv

WORKDIR /app
COPY --from=app-builder /app/.venv /app/.venv
```

Preserve the existing Gammu binary copy steps and runtime directory creation in the final stage.

- [ ] **Step 5: Run the runtime contract tests**

Run: `UV_CACHE_DIR=.uv-cache uv run pytest tests/test_runtime_contract.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add Dockerfile Dockerfile.alpine tests/test_runtime_contract.py
git commit -m "build: resolve package version from git in container builds"
```

## Task 4: Document the manual tag release flow and verify the end-to-end contract

**Files:**
- Modify: `README.md`
- Modify: `tests/test_runtime_contract.py`
- Test: `tests/test_runtime_contract.py`

- [ ] **Step 1: Write the failing README contract assertions**

Extend `test_readme_documents_local_quadlet_deploy_tracking` so it also enforces the manual tag release contract.

```python
def test_readme_documents_local_quadlet_deploy_tracking():
    readme = Path("README.md").read_text()
    assert "uv sync" in readme
    assert "uv run pytest" in readme
    assert "Git tags" in readme
    assert "v1.2.3" in readme
    assert "development version" in readme
    assert ".deploy/sms-to-telegram-state.json" in readme
```

- [ ] **Step 2: Run the README contract test to verify it fails**

Run: `UV_CACHE_DIR=.uv-cache uv run pytest tests/test_runtime_contract.py::test_readme_documents_local_quadlet_deploy_tracking -v`
Expected: FAIL because the README does not yet explain the Git-tag-driven version contract.

- [ ] **Step 3: Update the README**

Add a short release/versioning section that documents:

- release tags are created manually in `vX.Y.Z` form
- non-tagged commits build as development versions derived from the most recent tag
- container builds require Git metadata during the install step but the final runtime image does not include `.git`
- local deploy rebuild detection now includes Git version state, so new tags and other version-affecting Git changes trigger a rebuild

Use concrete commands:

```bash
git tag v1.2.3
git push origin v1.2.3
```

- [ ] **Step 4: Run full verification**

Run: `bash -n setup.sh entrypoint.sh`
Expected: no output

Run: `UV_CACHE_DIR=.uv-cache uv run pytest -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add README.md tests/test_runtime_contract.py
git commit -m "docs: describe git-based versioning contract"
```

## Final Verification

- [ ] Run: `git status --short`
Expected: clean working tree

- [ ] Run: `UV_CACHE_DIR=.uv-cache uv run pytest -v`
Expected: PASS

- [ ] Run: `bash -n setup.sh entrypoint.sh`
Expected: no output

- [ ] Run: `git log --oneline -4`
Expected: the recent history includes the packaging, setup fingerprint, container build, and README commits from this plan
