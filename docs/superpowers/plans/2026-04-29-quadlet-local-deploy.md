# Quadlet Local Deploy Tracking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restructure `setup.sh` into a local deploy command that builds a local Podman image only when image-relevant files changed, installs the Quadlet unit into `/etc/containers/systemd/`, restarts the service, and records local deploy state inside the repo.

**Architecture:** Keep one stable local image name, `localhost/sms-to-telegram:latest`, in the Quadlet file. Move deploy tracking into a gitignored repo-local state file under `.deploy/`, and make `setup.sh` compute a fingerprint from image-relevant files, inspect local image existence, build only when required, then perform the systemd/Quadlet integration steps and record deployed state.

**Tech Stack:** Bash, Podman, systemd/Quadlet, Python `pytest`

---

## File Structure

- Modify: `.gitignore` to ignore local deploy state
- Modify: `setup.sh` to own fingerprinting, build-or-skip logic, deploy-state persistence, and privileged system integration
- Modify: `sms-to-telegram.container` only if comments or formatting need to align with the local-image deploy flow
- Modify: `README.md` to document the local-image + deploy-state setup flow
- Create: `tests/test_setup_script.py` for deploy-state and build/deploy decision coverage

### Task 1: Add Local Deploy State Tracking And Fingerprint Tests

**Files:**
- Modify: `.gitignore`
- Modify: `setup.sh`
- Create: `tests/test_setup_script.py`

- [ ] **Step 1: Write the failing tests for fingerprint inputs and state-file persistence**

Create `tests/test_setup_script.py` with:

```python
import json
import os
import subprocess
from pathlib import Path


def write_fake_bin(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text(body)
    path.chmod(0o755)


def prepare_repo_copy(tmp_path: Path, repo_root: Path) -> Path:
    target = tmp_path / "repo"
    subprocess.run(["cp", "-R", str(repo_root), str(target)], check=True)
    return target


def test_setup_creates_local_state_after_first_build(tmp_path):
    repo_root = Path.cwd()
    repo = prepare_repo_copy(tmp_path, repo_root)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "calls.log"

    write_fake_bin(
        fake_bin,
        "podman",
        "#!/bin/sh\n"
        "echo \"podman:$@\" >> \"$CALLS_LOG\"\n"
        "if [ \"$1\" = image ] && [ \"$2\" = exists ]; then exit 1; fi\n"
        "if [ \"$1\" = image ] && [ \"$2\" = inspect ]; then echo 'sha256:test-image'; exit 0; fi\n"
        "exit 0\n",
    )
    write_fake_bin(
        fake_bin,
        "sudo",
        "#!/bin/sh\n"
        "echo \"sudo:$@\" >> \"$CALLS_LOG\"\n"
        "shift\n"
        "exec \"$@\"\n",
    )
    write_fake_bin(
        fake_bin,
        "systemctl",
        "#!/bin/sh\n"
        "echo \"systemctl:$@\" >> \"$CALLS_LOG\"\n"
        "exit 0\n",
    )
    write_fake_bin(
        fake_bin,
        "install",
        "#!/bin/sh\n"
        "/usr/bin/install \"$@\"\n",
    )

    env = os.environ | {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "CALLS_LOG": str(log),
        "QUADLET_DIR": str(tmp_path / "quadlet"),
        "STATE_DIR": str(repo / ".deploy"),
        "IMAGE_NAME": "localhost/sms-to-telegram:latest",
    }

    result = subprocess.run(["bash", "setup.sh"], cwd=repo, env=env, capture_output=True, text=True)

    assert result.returncode == 0
    state = json.loads((repo / ".deploy" / "sms-to-telegram-state.json").read_text())
    assert state["image"] == "localhost/sms-to-telegram:latest"
    assert state["image_id"] == "sha256:test-image"
    assert state["source_fingerprint"]
    assert state["last_built_at"]
    assert state["last_deployed_at"]


def test_setup_fingerprint_changes_only_when_runtime_inputs_change(tmp_path):
    repo_root = Path.cwd()
    repo = prepare_repo_copy(tmp_path, repo_root)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "calls.log"

    write_fake_bin(
        fake_bin,
        "podman",
        "#!/bin/sh\n"
        "echo \"podman:$@\" >> \"$CALLS_LOG\"\n"
        "if [ \"$1\" = image ] && [ \"$2\" = exists ]; then exit 0; fi\n"
        "if [ \"$1\" = image ] && [ \"$2\" = inspect ]; then echo 'sha256:existing-image'; exit 0; fi\n"
        "exit 0\n",
    )
    write_fake_bin(
        fake_bin,
        "sudo",
        "#!/bin/sh\n"
        "shift\n"
        "exec \"$@\"\n",
    )
    write_fake_bin(
        fake_bin,
        "systemctl",
        "#!/bin/sh\n"
        "exit 0\n",
    )
    write_fake_bin(
        fake_bin,
        "install",
        "#!/bin/sh\n"
        "/usr/bin/install \"$@\"\n",
    )

    state_dir = repo / ".deploy"
    state_dir.mkdir()
    state_file = state_dir / "sms-to-telegram-state.json"
    first = {
        "image": "localhost/sms-to-telegram:latest",
        "image_id": "sha256:existing-image",
        "source_fingerprint": "",
        "last_built_at": "2026-04-29T18:00:00+00:00",
        "last_deployed_at": "2026-04-29T18:01:00+00:00",
    }
    state_file.write_text(json.dumps(first))

    env = os.environ | {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "CALLS_LOG": str(log),
        "QUADLET_DIR": str(tmp_path / "quadlet"),
        "STATE_DIR": str(state_dir),
        "IMAGE_NAME": "localhost/sms-to-telegram:latest",
    }

    initial = subprocess.run(["bash", "setup.sh", "--print-fingerprint"], cwd=repo, env=env, capture_output=True, text=True, check=True).stdout.strip()
    (repo / "README.md").write_text((repo / "README.md").read_text() + "\nlocal docs change\n")
    after_docs = subprocess.run(["bash", "setup.sh", "--print-fingerprint"], cwd=repo, env=env, capture_output=True, text=True, check=True).stdout.strip()
    (repo / "entrypoint.sh").write_text((repo / "entrypoint.sh").read_text() + "\n# runtime change\n")
    after_runtime = subprocess.run(["bash", "setup.sh", "--print-fingerprint"], cwd=repo, env=env, capture_output=True, text=True, check=True).stdout.strip()

    assert after_docs == initial
    assert after_runtime != initial
```

- [ ] **Step 2: Run the new setup-script tests and verify they fail for the right reason**

Run: `./.venv/bin/python -m pytest tests/test_setup_script.py -v`
Expected: FAIL because `setup.sh` does not yet support local state, `--print-fingerprint`, or the configurable deploy environment.

- [ ] **Step 3: Add the minimal deploy-state and fingerprint structure**

Update `.gitignore` to include:

```gitignore
.deploy/
```

Replace `setup.sh` with this structure:

```bash
#!/bin/bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
STATE_DIR="${STATE_DIR:-$REPO_ROOT/.deploy}"
STATE_FILE="$STATE_DIR/sms-to-telegram-state.json"
IMAGE_NAME="${IMAGE_NAME:-localhost/sms-to-telegram:latest}"
QUADLET_SOURCE="${QUADLET_SOURCE:-$REPO_ROOT/sms-to-telegram.container}"
QUADLET_DIR="${QUADLET_DIR:-/etc/containers/systemd}"
QUADLET_TARGET="$QUADLET_DIR/sms-to-telegram.container"

IMAGE_INPUTS=(
  Dockerfile
  entrypoint.sh
  gammurc
  enqueue_sms.py
  send_worker.py
  check_forwarder_health.py
)

load_previous_fingerprint() {
  if [ -f "$STATE_FILE" ]; then
    python3 - <<'PY' "$STATE_FILE"
import json, sys
try:
    print(json.load(open(sys.argv[1])).get("source_fingerprint", ""))
except Exception:
    print("")
PY
  fi
}

compute_source_fingerprint() {
  (
    cd "$REPO_ROOT"
    {
      for path in "${IMAGE_INPUTS[@]}"; do
        printf '%s\0' "$path"
        cat "$path"
      done
      find sms_forwarder -type f | sort | while read -r path; do
        printf '%s\0' "$path"
        cat "$path"
      done
    } | shasum -a 256 | awk '{print $1}'
  )
}

if [ "${1:-}" = "--print-fingerprint" ]; then
  compute_source_fingerprint
  exit 0
fi
```

- [ ] **Step 4: Run the setup-script tests again**

Run: `./.venv/bin/python -m pytest tests/test_setup_script.py -v`
Expected: FAIL later in the flow, but now `--print-fingerprint` exists and `.deploy/` is ignored.

- [ ] **Step 5: Commit the state-tracking foundation**

```bash
git add .gitignore setup.sh tests/test_setup_script.py
git commit -m "test: add deploy state tracking foundation"
```

### Task 2: Add Build-Or-Skip Deployment Flow

**Files:**
- Modify: `setup.sh`
- Test: `tests/test_setup_script.py`

- [ ] **Step 1: Extend the failing tests for image-missing, fingerprint-unchanged, and rebuild-on-runtime-change cases**

Append to `tests/test_setup_script.py`:

```python
def test_setup_skips_build_when_image_exists_and_fingerprint_is_unchanged(tmp_path):
    repo_root = Path.cwd()
    repo = prepare_repo_copy(tmp_path, repo_root)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "calls.log"

    write_fake_bin(
        fake_bin,
        "podman",
        "#!/bin/sh\n"
        "echo \"podman:$@\" >> \"$CALLS_LOG\"\n"
        "if [ \"$1\" = image ] && [ \"$2\" = exists ]; then exit 0; fi\n"
        "if [ \"$1\" = image ] && [ \"$2\" = inspect ]; then echo 'sha256:existing-image'; exit 0; fi\n"
        "exit 0\n",
    )
    write_fake_bin(fake_bin, "sudo", "#!/bin/sh\nshift\nexec \"$@\"\n")
    write_fake_bin(fake_bin, "systemctl", "#!/bin/sh\necho \"systemctl:$@\" >> \"$CALLS_LOG\"\nexit 0\n")
    write_fake_bin(fake_bin, "install", "#!/bin/sh\n/usr/bin/install \"$@\"\n")

    state_dir = repo / ".deploy"
    state_dir.mkdir()
    fingerprint = subprocess.run(
        ["bash", "setup.sh", "--print-fingerprint"],
        cwd=repo,
        env=os.environ | {"PATH": f"{fake_bin}:{os.environ['PATH']}"},
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    (state_dir / "sms-to-telegram-state.json").write_text(json.dumps({
        "image": "localhost/sms-to-telegram:latest",
        "image_id": "sha256:existing-image",
        "source_fingerprint": fingerprint,
        "last_built_at": "2026-04-29T18:00:00+00:00",
        "last_deployed_at": "2026-04-29T18:01:00+00:00",
    }))

    env = os.environ | {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "CALLS_LOG": str(log),
        "QUADLET_DIR": str(tmp_path / "quadlet"),
        "STATE_DIR": str(state_dir),
        "IMAGE_NAME": "localhost/sms-to-telegram:latest",
    }

    result = subprocess.run(["bash", "setup.sh"], cwd=repo, env=env, capture_output=True, text=True)

    assert result.returncode == 0
    assert "build skipped: fingerprint unchanged" in result.stdout
    assert "podman:build" not in log.read_text()


def test_setup_rebuilds_when_runtime_input_changes(tmp_path):
    repo_root = Path.cwd()
    repo = prepare_repo_copy(tmp_path, repo_root)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "calls.log"

    write_fake_bin(
        fake_bin,
        "podman",
        "#!/bin/sh\n"
        "echo \"podman:$@\" >> \"$CALLS_LOG\"\n"
        "if [ \"$1\" = image ] && [ \"$2\" = exists ]; then exit 0; fi\n"
        "if [ \"$1\" = image ] && [ \"$2\" = inspect ]; then echo 'sha256:new-image'; exit 0; fi\n"
        "exit 0\n",
    )
    write_fake_bin(fake_bin, "sudo", "#!/bin/sh\nshift\nexec \"$@\"\n")
    write_fake_bin(fake_bin, "systemctl", "#!/bin/sh\nexit 0\n")
    write_fake_bin(fake_bin, "install", "#!/bin/sh\n/usr/bin/install \"$@\"\n")

    state_dir = repo / ".deploy"
    state_dir.mkdir()
    (state_dir / "sms-to-telegram-state.json").write_text(json.dumps({
        "image": "localhost/sms-to-telegram:latest",
        "image_id": "sha256:old-image",
        "source_fingerprint": "stale",
        "last_built_at": "2026-04-29T18:00:00+00:00",
        "last_deployed_at": "2026-04-29T18:01:00+00:00",
    }))

    env = os.environ | {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "CALLS_LOG": str(log),
        "QUADLET_DIR": str(tmp_path / "quadlet"),
        "STATE_DIR": str(state_dir),
        "IMAGE_NAME": "localhost/sms-to-telegram:latest",
    }

    result = subprocess.run(["bash", "setup.sh"], cwd=repo, env=env, capture_output=True, text=True)

    assert result.returncode == 0
    assert "build triggered: source fingerprint changed" in result.stdout
    assert "podman:build" in log.read_text()
```

- [ ] **Step 2: Run the setup-script tests to verify they fail**

Run: `./.venv/bin/python -m pytest tests/test_setup_script.py -v`
Expected: FAIL because `setup.sh` still lacks build-or-skip orchestration, Podman image existence checks, and deploy-state writes.

- [ ] **Step 3: Implement the build/deploy orchestration in `setup.sh`**

Extend `setup.sh` with:

```bash
image_exists() {
  podman image exists "$IMAGE_NAME"
}

inspect_image_id() {
  podman image inspect "$IMAGE_NAME" --format '{{.Id}}'
}

write_state_file() {
  local fingerprint="$1"
  local image_id="$2"
  local built_at="$3"
  local deployed_at="$4"
  mkdir -p "$STATE_DIR"
  python3 - <<'PY' "$STATE_FILE" "$IMAGE_NAME" "$image_id" "$fingerprint" "$built_at" "$deployed_at"
import json, sys
from pathlib import Path
state = {
    "image": sys.argv[2],
    "image_id": sys.argv[3],
    "source_fingerprint": sys.argv[4],
    "last_built_at": sys.argv[5],
    "last_deployed_at": sys.argv[6],
}
Path(sys.argv[1]).write_text(json.dumps(state, indent=2) + "\n")
PY
}

install_quadlet_unit() {
  sudo install -D -m 0644 "$QUADLET_SOURCE" "$QUADLET_TARGET"
}

restart_service() {
  sudo systemctl daemon-reload
  sudo systemctl restart sms-to-telegram.service
}

main() {
  local fingerprint previous_fingerprint reason built_at image_id deployed_at
  fingerprint="$(compute_source_fingerprint)"
  previous_fingerprint="$(load_previous_fingerprint)"
  built_at=""

  if ! image_exists; then
    reason="image missing"
  elif [ "$fingerprint" != "$previous_fingerprint" ]; then
    reason="source fingerprint changed"
  else
    reason=""
  fi

  if [ -n "$reason" ]; then
    echo "build triggered: $reason"
    podman build -t "$IMAGE_NAME" .
    built_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  else
    echo "build skipped: fingerprint unchanged"
  fi

  image_id="$(inspect_image_id)"
  install_quadlet_unit
  restart_service
  deployed_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  write_state_file "$fingerprint" "$image_id" "${built_at:-$deployed_at}" "$deployed_at"
  echo "deployed image: $image_id"
}

main "$@"
```

- [ ] **Step 4: Run the setup-script tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/test_setup_script.py -v`
Expected: PASS for first-build, unchanged-fingerprint skip, and runtime-change rebuild cases.

- [ ] **Step 5: Commit the deploy orchestration**

```bash
git add setup.sh tests/test_setup_script.py
git commit -m "feat: add local Quadlet deploy tracking"
```

### Task 3: Document The Local Quadlet Deploy Flow

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Write the failing documentation contract checks**

Add this test to `tests/test_runtime_contract.py`:

```python
def test_readme_documents_local_quadlet_deploy_tracking():
    readme = Path("README.md").read_text()
    assert "localhost/sms-to-telegram:latest" in readme
    assert ".deploy/sms-to-telegram-state.json" in readme
    assert "build skipped: fingerprint unchanged" in readme
```

- [ ] **Step 2: Run the contract tests to verify they fail**

Run: `./.venv/bin/python -m pytest tests/test_runtime_contract.py -v`
Expected: FAIL because the README does not yet describe local deploy-state tracking.

- [ ] **Step 3: Update `README.md` with the local deploy flow**

Add a section covering:

```markdown
## Local Quadlet Deploy Flow

`setup.sh` is the local deploy entrypoint.

It:

1. computes a fingerprint from image-relevant files
2. checks whether `localhost/sms-to-telegram:latest` already exists
3. rebuilds only when the image is missing or the fingerprint changed
4. installs `sms-to-telegram.container` into `/etc/containers/systemd/`
5. reloads systemd and restarts `sms-to-telegram.service`
6. records local deploy state in `.deploy/sms-to-telegram-state.json`

Typical output:

- `build skipped: fingerprint unchanged`
- `build triggered: image missing`
- `build triggered: source fingerprint changed`
- `deployed image: sha256:...`
```

Also document that `.deploy/` is local-only and gitignored.

- [ ] **Step 4: Run the contract tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/test_runtime_contract.py -v`
Expected: PASS including the new README deploy-tracking check.

- [ ] **Step 5: Run the full test suite**

Run: `./.venv/bin/python -m pytest -v`
Expected: PASS for all existing tests plus `tests/test_setup_script.py`

- [ ] **Step 6: Commit the documentation update**

```bash
git add README.md tests/test_runtime_contract.py
git commit -m "docs: describe local Quadlet deploy tracking"
```
