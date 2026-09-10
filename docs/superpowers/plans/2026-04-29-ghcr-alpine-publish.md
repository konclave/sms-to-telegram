# GHCR Alpine Publish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a GitHub Actions release workflow that publishes only the Alpine image to GHCR on version tag pushes and document that release contract in the README.

**Architecture:** The implementation adds a single tag-triggered workflow under `.github/workflows/` that builds `Dockerfile.alpine`, logs into `ghcr.io` with `GITHUB_TOKEN`, and publishes `vX.Y.Z` plus normalized `X.Y.Z` tags for `ghcr.io/<owner>/sms-to-telegram`. A small runtime-contract-style test update and README change keep the workflow behavior and documentation aligned.

**Tech Stack:** GitHub Actions, Docker Buildx, GHCR, YAML, pytest, Markdown

---

## File Structure

### Existing files to modify

- `README.md`
  - Document the GHCR release contract: version-tag trigger, Alpine-only publishing, target image name, and tag format.
- `tests/test_runtime_contract.py`
  - Add assertions that lock in the workflow path, registry target, and README release contract.

### New files to create

- `.github/workflows/publish-ghcr.yml`
  - Tag-triggered GHCR publish workflow for `Dockerfile.alpine`.

## Task 1: Add workflow contract tests

**Files:**
- Modify: `tests/test_runtime_contract.py`
- Test: `tests/test_runtime_contract.py`

- [ ] **Step 1: Write the failing workflow contract tests**

Add tests that require a GHCR publish workflow and README release documentation.

```python
from pathlib import Path


def test_workflow_publishes_alpine_image_to_ghcr_on_version_tags():
    workflow = Path(".github/workflows/publish-ghcr.yml").read_text()

    assert "tags:" in workflow
    assert "- 'v*'" in workflow or '- \"v*\"' in workflow
    assert "ghcr.io" in workflow
    assert "Dockerfile.alpine" in workflow
    assert "packages: write" in workflow
    assert "docker/login-action" in workflow
    assert "docker/build-push-action" in workflow


def test_readme_documents_ghcr_alpine_release_contract():
    readme = Path("README.md").read_text()

    assert "ghcr.io/" in readme
    assert "Dockerfile.alpine" in readme
    assert "v1.2.3" in readme
    assert "GitHub Actions" in readme
```

- [ ] **Step 2: Run the targeted tests to verify they fail**

Run: `UV_CACHE_DIR=.uv-cache uv run pytest -v tests/test_runtime_contract.py`
Expected: FAIL because `.github/workflows/publish-ghcr.yml` does not exist yet and the README does not document the release contract.

- [ ] **Step 3: Commit the red test state if desired**

```bash
git diff -- tests/test_runtime_contract.py
```

Expected: only the new failing workflow/documentation contract tests are pending.

## Task 2: Implement the GHCR publish workflow

**Files:**
- Create: `.github/workflows/publish-ghcr.yml`
- Test: `tests/test_runtime_contract.py`

- [ ] **Step 1: Create the workflow skeleton**

Create `.github/workflows/publish-ghcr.yml` with tag trigger and package-write permissions.

```yaml
name: Publish Alpine Image to GHCR

on:
  push:
    tags:
      - "v*"

permissions:
  contents: read
  packages: write

jobs:
  publish:
    runs-on: ubuntu-latest
```

- [ ] **Step 2: Add checkout, tag derivation, registry login, and build-push**

Expand the workflow so it:

1. checks out the repo
2. derives `VERSION_TAG` and `NORMALIZED_TAG`
3. logs into `ghcr.io` with `${{ github.actor }}` and `${{ secrets.GITHUB_TOKEN }}`
4. builds `Dockerfile.alpine`
5. pushes `ghcr.io/${{ github.repository_owner }}/sms-to-telegram:${VERSION_TAG}` and `...:${NORMALIZED_TAG}`
6. applies OCI labels for source, revision, and version

Target shape:

```yaml
name: Publish Alpine Image to GHCR

on:
  push:
    tags:
      - "v*"

permissions:
  contents: read
  packages: write

jobs:
  publish:
    runs-on: ubuntu-latest
    steps:
      - name: Check out repository
        uses: actions/checkout@v4

      - name: Derive image tags
        run: |
          VERSION_TAG="${GITHUB_REF_NAME}"
          NORMALIZED_TAG="${VERSION_TAG#v}"
          {
            echo "VERSION_TAG=${VERSION_TAG}"
            echo "NORMALIZED_TAG=${NORMALIZED_TAG}"
            echo "IMAGE_NAME=ghcr.io/${GITHUB_REPOSITORY_OWNER,,}/sms-to-telegram"
          } >> "$GITHUB_ENV"

      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3

      - name: Log in to GHCR
        uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Build and push Alpine image
        uses: docker/build-push-action@v6
        with:
          context: .
          file: Dockerfile.alpine
          push: true
          tags: |
            ${{ env.IMAGE_NAME }}:${{ env.VERSION_TAG }}
            ${{ env.IMAGE_NAME }}:${{ env.NORMALIZED_TAG }}
          labels: |
            org.opencontainers.image.source=${{ github.server_url }}/${{ github.repository }}
            org.opencontainers.image.revision=${{ github.sha }}
            org.opencontainers.image.version=${{ env.VERSION_TAG }}
```

- [ ] **Step 3: Run the targeted runtime-contract tests**

Run: `UV_CACHE_DIR=.uv-cache uv run pytest -v tests/test_runtime_contract.py`
Expected: PASS for the new workflow contract checks, with any pre-existing conditional container runtime tests still passing or skipping.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/publish-ghcr.yml tests/test_runtime_contract.py
git commit -m "feat: publish alpine image to ghcr"
```

## Task 3: Document the release contract in the README

**Files:**
- Modify: `README.md`
- Modify: `tests/test_runtime_contract.py`
- Test: `tests/test_runtime_contract.py`

- [ ] **Step 1: Tighten the README contract assertions if needed**

Make sure `tests/test_runtime_contract.py` requires the exact README facts this task must preserve.

```python
def test_readme_documents_ghcr_alpine_release_contract():
    readme = Path("README.md").read_text()

    assert "GitHub Actions" in readme
    assert "ghcr.io/<owner>/sms-to-telegram" in readme
    assert "Dockerfile.alpine" in readme
    assert "v1.2.3" in readme
    assert "version tags" in readme
```

- [ ] **Step 2: Run the README-focused test to verify it fails if still needed**

Run: `UV_CACHE_DIR=.uv-cache uv run pytest -v tests/test_runtime_contract.py::test_readme_documents_ghcr_alpine_release_contract`
Expected: FAIL until the README text matches the new GHCR contract.

- [ ] **Step 3: Update the README**

Add a small section that explains:

- GHCR publishing is handled by GitHub Actions
- only the Alpine image built from `Dockerfile.alpine` is published
- publishing is triggered by pushed version tags like `v1.2.3`
- the target image name is `ghcr.io/<owner>/sms-to-telegram`
- both `v1.2.3` and `1.2.3` tags are published

Target shape:

```md
## GHCR Publishing

GitHub Actions publishes the Alpine image to `ghcr.io/<owner>/sms-to-telegram`.

Release publishing is triggered by pushed version tags such as `v1.2.3`.

The workflow builds only `Dockerfile.alpine` and publishes at least:

- `ghcr.io/<owner>/sms-to-telegram:v1.2.3`
- `ghcr.io/<owner>/sms-to-telegram:1.2.3`
```

- [ ] **Step 4: Run the runtime-contract tests again**

Run: `UV_CACHE_DIR=.uv-cache uv run pytest -v tests/test_runtime_contract.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add README.md tests/test_runtime_contract.py
git commit -m "docs: describe ghcr alpine publishing"
```

## Task 4: Final verification

**Files:**
- Verify only: `.github/workflows/publish-ghcr.yml`
- Verify only: `README.md`
- Verify only: `tests/test_runtime_contract.py`

- [ ] **Step 1: Validate workflow YAML and static contract**

Run: `sed -n '1,240p' .github/workflows/publish-ghcr.yml`
Expected: tag trigger is `v*`, `Dockerfile.alpine` is used, `ghcr.io` is the registry target, and `packages: write` is present.

- [ ] **Step 2: Run the full Python test suite**

Run: `UV_CACHE_DIR=.uv-cache uv run pytest -v`
Expected: PASS, with any container-runtime tests either passing or skipping if no usable local container engine is available.

- [ ] **Step 3: Record the known limitation**

Document in the final handoff that local GitHub Actions execution and live GHCR publishing were not exercised from this environment.

- [ ] **Step 4: Commit if any final cleanups were needed**

```bash
git status --short
```

Expected: no uncommitted changes beyond intentionally untracked local artifacts.

## Self-Review

- Spec coverage:
  - tag-triggered GHCR workflow: Task 2
  - Alpine-only publishing: Task 2 and Task 3
  - version and normalized tags: Task 2
  - README release contract: Task 3
  - static verification and final test pass: Task 4
- Placeholder scan:
  - no `TBD`, `TODO`, or “similar to above” shortcuts remain
- Type consistency:
  - image name is consistently `ghcr.io/<owner>/sms-to-telegram`
  - trigger is consistently version tags like `v1.2.3`
  - the published image variant is consistently `Dockerfile.alpine`
