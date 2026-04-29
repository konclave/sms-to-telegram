# Python 3.14 And uv Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert the repo into a `uv`-managed Python package, move both container variants to Python `3.14`, and preserve the Quadlet deploy flow with updated runtime contracts.

**Architecture:** The migration keeps `sms_forwarder/` as the core package, introduces `pyproject.toml` and `uv.lock` as the source of truth for dependencies, and replaces path-based runtime hooks with installed console commands. The deploy script continues to build and install `localhost/sms-to-telegram:latest`, but its fingerprint expands to include packaging inputs so local deploy tracking stays correct.

**Tech Stack:** Python 3.14, uv, pytest, Podman/Quadlet, shell entrypoints, Docker/Containerfiles

---

## File Structure

### Existing files to modify

- `README.md`
  - Document `uv sync`, `uv run pytest`, packaged console commands, and the updated image build/runtime model.
- `Dockerfile`
  - Move the primary image to a Python `3.14` capable base and install the package through `uv`.
- `Dockerfile.alpine`
  - Keep the Alpine variant, but switch it to a Python `3.14` capable runtime and install the same packaged app.
- `entrypoint.sh`
  - Replace direct `/usr/bin/send_worker.py` invocation with the installed worker command.
- `gammurc`
  - Replace `/usr/bin/enqueue_sms.py` with the installed enqueue command.
- `setup.sh`
  - Track `pyproject.toml`, `uv.lock`, and any new runtime wrapper files in the image fingerprint.
- `tests/test_runtime_contract.py`
  - Assert command-based runtime wiring and updated README guidance.
- `tests/test_setup_script.py`
  - Assert fingerprint changes when packaging files change and stays stable for unrelated docs changes.

### Existing files likely to delete

- `requirements-dev.txt`
  - Replaced by `pyproject.toml` and `uv.lock`.

### Existing files to keep, but potentially refactor internally

- `sms_forwarder/enqueue_hook.py`
- `sms_forwarder/worker.py`
- `sms_forwarder/healthcheck.py`
  - Provide callable `main()` entry points suitable for console scripts.

### New files to create

- `pyproject.toml`
  - Project metadata, Python `3.14` requirement, runtime and dev dependencies, and console script entry points.
- `uv.lock`
  - Locked dependency graph for `uv`.
- `.python-version`
  - Pin local development to `3.14`.

## Task 1: Introduce uv-managed packaging metadata

**Files:**
- Create: `pyproject.toml`
- Create: `.python-version`
- Create: `uv.lock`
- Delete: `requirements-dev.txt`
- Modify: `sms_forwarder/enqueue_hook.py`
- Modify: `sms_forwarder/worker.py`
- Modify: `sms_forwarder/healthcheck.py`
- Test: `tests/test_enqueue_hook.py`
- Test: `tests/test_worker.py`
- Test: `tests/test_healthcheck.py`

- [ ] **Step 1: Write the failing packaging contract test**

Add a new test to `tests/test_runtime_contract.py` that proves the repo has moved to `pyproject.toml` and no longer relies on `requirements-dev.txt`.

```python
from pathlib import Path


def test_repo_uses_uv_packaging_metadata():
    pyproject = Path("pyproject.toml").read_text()
    assert 'requires-python = ">=3.14,<3.15"' in pyproject
    assert '"pytest"' in pyproject
    assert '[project.scripts]' in pyproject
    assert not Path("requirements-dev.txt").exists()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m pytest tests/test_runtime_contract.py::test_repo_uses_uv_packaging_metadata -v`
Expected: FAIL because `pyproject.toml` and `uv.lock` do not exist yet, and `requirements-dev.txt` still exists.

- [ ] **Step 3: Add project metadata and console entry points**

Create `pyproject.toml` with setuptools packaging, Python `3.14` pinning, pytest as the dev dependency, and console scripts that call `main()` functions from the existing modules.

```toml
[build-system]
requires = ["setuptools>=80"]
build-backend = "setuptools.build_meta"

[project]
name = "sms-to-telegram"
version = "0.1.0"
description = "Forward incoming SMS messages to Telegram with a durable local queue."
requires-python = ">=3.14,<3.15"
dependencies = []

[project.optional-dependencies]
dev = ["pytest==8.4.1"]

[project.scripts]
sms-forwarder-enqueue = "sms_forwarder.enqueue_hook:main"
sms-forwarder-worker = "sms_forwarder.worker:main"
sms-forwarder-healthcheck = "sms_forwarder.healthcheck:main"

[tool.setuptools]
packages = ["sms_forwarder"]
```

Create `.python-version`:

```text
3.14
```

Delete `requirements-dev.txt`.

- [ ] **Step 4: Add callable module entry points**

Ensure each runtime module exposes a no-argument `main()` that wraps its current CLI behavior without changing semantics.

Example shape for `sms_forwarder/enqueue_hook.py`:

```python
def main() -> int:
    # current environment-driven enqueue logic
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Use the same pattern in `sms_forwarder/worker.py` and `sms_forwarder/healthcheck.py`.

- [ ] **Step 5: Generate the uv lockfile**

Run: `uv lock`
Expected: `uv.lock` created and aligned with `pyproject.toml`.

- [ ] **Step 6: Run the targeted tests**

Run: `uv run pytest tests/test_runtime_contract.py::test_repo_uses_uv_packaging_metadata tests/test_enqueue_hook.py tests/test_worker.py tests/test_healthcheck.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml .python-version uv.lock sms_forwarder/enqueue_hook.py sms_forwarder/worker.py sms_forwarder/healthcheck.py tests/test_runtime_contract.py tests/test_enqueue_hook.py tests/test_worker.py tests/test_healthcheck.py
git rm requirements-dev.txt
git commit -m "feat: package app with uv"
```

## Task 2: Switch runtime wiring to installed commands

**Files:**
- Modify: `entrypoint.sh`
- Modify: `gammurc`
- Modify: `tests/test_runtime_contract.py`
- Test: `tests/test_runtime_contract.py`

- [ ] **Step 1: Write the failing runtime contract assertions**

Update `tests/test_runtime_contract.py` so it asserts installed command names instead of copied Python file paths.

```python
def test_runtime_files_reference_installed_forwarder_commands():
    gammurc = Path("gammurc").read_text()
    assert "RunOnReceive=sms-forwarder-enqueue" in gammurc

    entrypoint = Path("entrypoint.sh").read_text()
    assert "sms-forwarder-worker &" in entrypoint
    assert "WORKER_PID_FILE=${WORKER_PID_FILE:-/var/run/sms-forwarder-worker.pid}" in entrypoint
```

- [ ] **Step 2: Run the runtime contract test to verify it fails**

Run: `uv run pytest tests/test_runtime_contract.py::test_runtime_files_reference_installed_forwarder_commands -v`
Expected: FAIL because the files still reference `/usr/bin/enqueue_sms.py` and `/usr/bin/send_worker.py`.

- [ ] **Step 3: Update the runtime files**

Change `gammurc`:

```ini
RunOnReceive=sms-forwarder-enqueue
```

Change `entrypoint.sh`:

```sh
sms-forwarder-worker &
worker_pid=$!
printf '%s\n' "$worker_pid" > "$WORKER_PID_FILE"
```

Do not change the process supervision behavior beyond swapping the command path.

- [ ] **Step 4: Run the runtime contract test**

Run: `uv run pytest tests/test_runtime_contract.py::test_runtime_files_reference_installed_forwarder_commands -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add entrypoint.sh gammurc tests/test_runtime_contract.py
git commit -m "refactor: use installed forwarder commands"
```

## Task 3: Move both container variants to Python 3.14 and uv installation

**Files:**
- Modify: `Dockerfile`
- Modify: `Dockerfile.alpine`
- Test: `tests/test_runtime_contract.py`

- [ ] **Step 1: Write the failing container contract assertions**

Extend `tests/test_runtime_contract.py` with assertions for Python `3.14` and `uv`-based installation.

```python
def test_containerfiles_install_packaged_app_with_uv():
    dockerfile = Path("Dockerfile").read_text()
    assert "python:3.14" in dockerfile
    assert "uv sync --frozen --no-dev" in dockerfile
    assert "sms-forwarder-healthcheck" in dockerfile

    alpine = Path("Dockerfile.alpine").read_text()
    assert "python:3.14-alpine" in alpine
    assert "uv sync --frozen --no-dev" in alpine
```

- [ ] **Step 2: Run the container contract test to verify it fails**

Run: `uv run pytest tests/test_runtime_contract.py::test_containerfiles_install_packaged_app_with_uv -v`
Expected: FAIL because the current images install `python3` through distro packages and copy loose scripts.

- [ ] **Step 3: Rewrite the primary Dockerfile around Python 3.14**

Use a Python `3.14` capable base image, install non-Python runtime dependencies needed by `gammu-smsd`, install `uv`, then install the packaged app from project metadata before copying the rest of the runtime assets.

Target shape:

```dockerfile
FROM python:3.14-bookworm

RUN apt-get update && \
    apt-get install --no-install-recommends -y gammu-smsd gettext locales ca-certificates && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
COPY sms_forwarder sms_forwarder
RUN uv sync --frozen --no-dev

COPY gammurc /etc/gammurc
COPY entrypoint.sh /usr/bin/entrypoint.sh
ENV PATH="/app/.venv/bin:${PATH}"

HEALTHCHECK CMD sms-forwarder-healthcheck
ENTRYPOINT ["entrypoint.sh"]
```

- [ ] **Step 4: Rewrite the Alpine Dockerfile around Python 3.14**

Keep the Gammu builder stage if still needed, but make the final runtime image Python `3.14` based and install the packaged app the same way.

Target shape:

```dockerfile
FROM python:3.14-alpine AS runtime

RUN apk add --no-cache libusb libcurl tzdata curl gettext ca-certificates
RUN pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
COPY sms_forwarder sms_forwarder
RUN uv sync --frozen --no-dev

COPY gammurc /etc/gammurc
COPY entrypoint.sh /usr/bin/entrypoint.sh
ENV PATH="/app/.venv/bin:${PATH}"

HEALTHCHECK CMD sms-forwarder-healthcheck
ENTRYPOINT ["entrypoint.sh"]
```

Preserve any Gammu binaries or shared library copy steps that are still required for the Alpine build.

- [ ] **Step 5: Run the container contract test**

Run: `uv run pytest tests/test_runtime_contract.py::test_containerfiles_install_packaged_app_with_uv -v`
Expected: PASS

- [ ] **Step 6: Build both images if the environment permits**

Run: `podman build -t localhost/sms-to-telegram:test .`
Expected: image build succeeds

Run: `podman build -f Dockerfile.alpine -t localhost/sms-to-telegram:alpine-test .`
Expected: image build succeeds

If Podman or registry access is unavailable, capture the exact failure and report it in the final handoff.

- [ ] **Step 7: Commit**

```bash
git add Dockerfile Dockerfile.alpine tests/test_runtime_contract.py
git commit -m "feat: move container images to python 3.14"
```

## Task 4: Update deploy tracking for packaging-aware rebuilds

**Files:**
- Modify: `setup.sh`
- Modify: `tests/test_setup_script.py`
- Test: `tests/test_setup_script.py`

- [ ] **Step 1: Write the failing deploy fingerprint test**

Add a new setup-script test that mutates `pyproject.toml` and confirms the fingerprint changes.

```python
def test_setup_fingerprint_changes_when_pyproject_changes(tmp_path):
    repo_root = Path.cwd()
    repo = prepare_repo_copy(tmp_path, repo_root)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    write_fake_bin(fake_bin, "podman", "#!/bin/sh\nexit 0\n")

    env = os.environ | {"PATH": f"{fake_bin}:{os.environ['PATH']}"}
    before = subprocess.run(
        ["bash", "setup.sh", "--print-fingerprint"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    pyproject = repo / "pyproject.toml"
    pyproject.write_text(pyproject.read_text() + "\n# packaging change\n")
    after = subprocess.run(
        ["bash", "setup.sh", "--print-fingerprint"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    assert after != before
```

- [ ] **Step 2: Run the fingerprint test to verify it fails**

Run: `uv run pytest tests/test_setup_script.py::test_setup_fingerprint_changes_when_pyproject_changes -v`
Expected: FAIL because `setup.sh` does not yet include `pyproject.toml` or `uv.lock` in `IMAGE_INPUTS`.

- [ ] **Step 3: Expand image fingerprint inputs**

Update `setup.sh` so `IMAGE_INPUTS` includes at least:

```bash
IMAGE_INPUTS=(
  Dockerfile
  Dockerfile.alpine
  pyproject.toml
  uv.lock
  .python-version
  entrypoint.sh
  gammurc
)
```

Keep the `find sms_forwarder -type f` walk so package source changes still affect the fingerprint.

- [ ] **Step 4: Run the setup test file**

Run: `uv run pytest tests/test_setup_script.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add setup.sh tests/test_setup_script.py
git commit -m "feat: track packaging inputs in deploy fingerprint"
```

## Task 5: Document and verify the final uv workflow

**Files:**
- Modify: `README.md`
- Modify: `tests/test_runtime_contract.py`
- Test: `tests/test_runtime_contract.py`
- Test: `tests/test_setup_script.py`

- [ ] **Step 1: Write the failing README assertions**

Extend `tests/test_runtime_contract.py` so the README must mention the new `uv` workflow and packaged commands.

```python
def test_readme_documents_uv_workflow_and_packaged_runtime():
    readme = Path("README.md").read_text()
    assert "uv sync" in readme
    assert "uv run pytest" in readme
    assert "sms-forwarder-enqueue" in readme
    assert "sms-forwarder-worker" in readme
    assert "Python 3.14" in readme
```

- [ ] **Step 2: Run the README assertion test to verify it fails**

Run: `uv run pytest tests/test_runtime_contract.py::test_readme_documents_uv_workflow_and_packaged_runtime -v`
Expected: FAIL because the README still documents `docker build` and `requirements-dev.txt`-era behavior only.

- [ ] **Step 3: Update the README**

Document:

- local setup with `uv sync`
- test execution with `uv run pytest`
- Python `3.14` as the local/runtime version
- that the runtime uses installed `sms-forwarder-enqueue`, `sms-forwarder-worker`, and `sms-forwarder-healthcheck` commands
- that `setup.sh` now tracks `pyproject.toml` and `uv.lock` as part of rebuild detection

Remove or rewrite any sections that still imply `requirements-dev.txt` or loose script execution is the supported path.

- [ ] **Step 4: Run full project verification**

Run: `bash -n entrypoint.sh`
Expected: no output, exit `0`

Run: `bash -n setup.sh`
Expected: no output, exit `0`

Run: `uv run pytest -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add README.md tests/test_runtime_contract.py
git commit -m "docs: describe uv and python 3.14 workflow"
```

## Self-Review

- Spec coverage:
  - packaging and `uv` workflow: Task 1
  - installed runtime commands: Task 2
  - Python `3.14` in both images: Task 3
  - deploy fingerprint updates: Task 4
  - README and verification: Task 5
- Placeholder scan:
  - no `TBD`, `TODO`, or “similar to above” shortcuts remain
- Type consistency:
  - command names are consistently `sms-forwarder-enqueue`, `sms-forwarder-worker`, and `sms-forwarder-healthcheck`
  - packaging files are consistently `pyproject.toml`, `uv.lock`, and `.python-version`
