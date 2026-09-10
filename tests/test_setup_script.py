import json
import os
import subprocess
from pathlib import Path
from uuid import uuid4


def write_fake_bin(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text(body)
    path.chmod(0o755)


def install_stub_body() -> str:
    return (
        "#!/bin/sh\n"
        "src=''\n"
        "dest=''\n"
        "for arg in \"$@\"; do\n"
        "  src=\"$dest\"\n"
        "  dest=\"$arg\"\n"
        "done\n"
        "mkdir -p \"$(dirname \"$dest\")\"\n"
        "cp \"$src\" \"$dest\"\n"
    )


def udevadm_stub_body() -> str:
    return (
        "#!/bin/sh\n"
        "[ -n \"$CALLS_LOG\" ] && echo \"udevadm:$@\" >> \"$CALLS_LOG\"\n"
        "exit 0\n"
    )


def prepare_repo_copy(tmp_path: Path, repo_root: Path) -> Path:
    target = tmp_path / "repo"
    subprocess.run(["cp", "-R", str(repo_root), str(target)], check=True)
    return target


def commit_empty(repo: Path, message: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=Test User", "-c", "user.email=test@example.com",
         "commit", "--allow-empty", "-m", message],
        cwd=repo,
        check=True,
        capture_output=True,
    )


def move_head_off_any_tag(repo: Path) -> None:
    """`git describe` does not change when a second tag is added to a commit
    that is already tagged, so these tests are only meaningful with HEAD off a
    release tag. Checkouts sitting exactly on one (a freshly tagged release)
    would otherwise fail spuriously."""
    commit_empty(repo, "move head off any release tag")


def unique_version_tag(repo: Path) -> str:
    while True:
        tag = f"v9.8.{int(uuid4().hex[:6], 16)}"
        listed = subprocess.run(
            ["git", "tag", "--list", tag],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        if not listed:
            return tag


def test_setup_creates_local_state_after_first_build(tmp_path):
    repo_root = Path.cwd()
    repo = prepare_repo_copy(tmp_path, repo_root)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    write_fake_bin(fake_bin, "udevadm", udevadm_stub_body())
    log = tmp_path / "calls.log"

    write_fake_bin(
        fake_bin,
        "podman",
        "#!/bin/sh\n"
        "echo \"podman:$@\" >> \"$CALLS_LOG\"\n"
        "echo \"pwd:$(pwd)\" >> \"$CALLS_LOG\"\n"
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
        install_stub_body(),
    )

    env = os.environ | {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "CALLS_LOG": str(log),
        "QUADLET_DIR": str(tmp_path / "quadlet"),
        "QUEUE_HOST_DIR": str(tmp_path / "queue"),
        "UDEV_RULE_DIR": str(tmp_path / "udev"),
        "SYSTEMD_UNIT_DIR": str(tmp_path / "units"),
        "STATE_DIR": str(repo / ".deploy"),
        "IMAGE_NAME": "localhost/sms-to-telegram:latest",
    }

    outside = tmp_path / "outside"
    outside.mkdir()
    result = subprocess.run(["bash", str(repo / "setup.sh")], cwd=outside, env=env, capture_output=True, text=True)

    assert result.returncode == 0
    state = json.loads((repo / ".deploy" / "sms-to-telegram-state.json").read_text())
    assert state["image"] == "localhost/sms-to-telegram:latest"
    assert state["image_id"] == "sha256:test-image"
    assert state["source_fingerprint"]
    assert state["last_built_at"]
    assert state["last_deployed_at"]
    log_text = log.read_text()
    assert f"pwd:{repo}" in log_text
    assert (
        f"sudo:-- install -D -m 0644 {repo / 'sms-to-telegram.container'} "
        f"{tmp_path / 'quadlet' / 'sms-to-telegram.container'}"
    ) in log_text


def test_setup_fingerprint_changes_for_runtime_and_packaging_inputs(tmp_path):
    repo_root = Path.cwd()
    repo = prepare_repo_copy(tmp_path, repo_root)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    write_fake_bin(fake_bin, "udevadm", udevadm_stub_body())
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
        install_stub_body(),
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
        "QUEUE_HOST_DIR": str(tmp_path / "queue"),
        "UDEV_RULE_DIR": str(tmp_path / "udev"),
        "SYSTEMD_UNIT_DIR": str(tmp_path / "units"),
        "STATE_DIR": str(state_dir),
        "IMAGE_NAME": "localhost/sms-to-telegram:latest",
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

    move_head_off_any_tag(repo)
    initial = fingerprint()
    (repo / "pyproject.toml").write_text((repo / "pyproject.toml").read_text() + "\n# packaging change\n")
    after_pyproject = fingerprint()
    (repo / "uv.lock").write_text((repo / "uv.lock").read_text() + "\n# lockfile change\n")
    after_uv_lock = fingerprint()
    (repo / "Dockerfile.alpine").write_text((repo / "Dockerfile.alpine").read_text() + "\n# alternate image change\n")
    after_alpine = fingerprint()
    (repo / "entrypoint.sh").write_text((repo / "entrypoint.sh").read_text() + "\n# runtime change\n")
    after_runtime = fingerprint()
    subprocess.run(["git", "tag", unique_version_tag(repo)], cwd=repo, check=True)
    after_tag = fingerprint()

    assert after_pyproject != initial
    assert after_uv_lock != after_pyproject
    assert after_alpine == after_uv_lock
    assert after_runtime != after_alpine
    assert after_tag != after_runtime


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

    move_head_off_any_tag(repo)
    initial = fingerprint()
    subprocess.run(["git", "tag", unique_version_tag(repo)], cwd=repo, check=True)
    after_tag = fingerprint()
    commit_empty(repo, "advance version state")
    after_commit = fingerprint()

    assert after_tag != initial
    assert after_commit != after_tag


def test_setup_skips_build_when_image_exists_and_fingerprint_is_unchanged(tmp_path):
    repo_root = Path.cwd()
    repo = prepare_repo_copy(tmp_path, repo_root)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    write_fake_bin(fake_bin, "udevadm", udevadm_stub_body())
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
    write_fake_bin(fake_bin, "install", install_stub_body())

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
        "QUEUE_HOST_DIR": str(tmp_path / "queue"),
        "UDEV_RULE_DIR": str(tmp_path / "udev"),
        "SYSTEMD_UNIT_DIR": str(tmp_path / "units"),
        "STATE_DIR": str(state_dir),
        "IMAGE_NAME": "localhost/sms-to-telegram:latest",
    }

    result = subprocess.run(["bash", "setup.sh"], cwd=repo, env=env, capture_output=True, text=True)

    assert result.returncode == 0
    assert "build skipped: fingerprint unchanged" in result.stdout
    assert "podman:build" not in log.read_text()
    updated_state = json.loads((state_dir / "sms-to-telegram-state.json").read_text())
    assert updated_state["last_built_at"] == "2026-04-29T18:00:00+00:00"


def test_setup_skips_build_for_remote_image(tmp_path):
    repo_root = Path.cwd()
    repo = prepare_repo_copy(tmp_path, repo_root)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    write_fake_bin(fake_bin, "udevadm", udevadm_stub_body())
    log = tmp_path / "calls.log"

    write_fake_bin(
        fake_bin,
        "podman",
        "#!/bin/sh\n"
        "echo \"podman:$@\" >> \"$CALLS_LOG\"\n"
        "if [ \"$1\" = image ] && [ \"$2\" = inspect ]; then echo 'sha256:remote-image'; exit 0; fi\n"
        "exit 0\n",
    )
    write_fake_bin(fake_bin, "sudo", "#!/bin/sh\nshift\nexec \"$@\"\n")
    write_fake_bin(fake_bin, "systemctl", "#!/bin/sh\nexit 0\n")
    write_fake_bin(fake_bin, "install", install_stub_body())

    env = os.environ | {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "CALLS_LOG": str(log),
        "QUADLET_DIR": str(tmp_path / "quadlet"),
        "QUEUE_HOST_DIR": str(tmp_path / "queue"),
        "UDEV_RULE_DIR": str(tmp_path / "udev"),
        "SYSTEMD_UNIT_DIR": str(tmp_path / "units"),
        "STATE_DIR": str(repo / ".deploy"),
        "IMAGE_NAME": "ghcr.io/konclave/sms-to-telegram:latest",
    }

    result = subprocess.run(["bash", str(repo / "setup.sh")], cwd=tmp_path, env=env, capture_output=True, text=True)

    assert result.returncode == 0
    assert "pulling remote image ghcr.io/konclave/sms-to-telegram:latest" in result.stdout

    calls = log.read_text()
    assert "podman:pull ghcr.io/konclave/sms-to-telegram:latest" in calls
    assert "podman:build" not in calls
    state = json.loads((repo / ".deploy" / "sms-to-telegram-state.json").read_text())
    assert state["image"] == "ghcr.io/konclave/sms-to-telegram:latest"
    assert state["image_id"] == "sha256:remote-image"


def test_setup_rebuilds_when_runtime_input_changes(tmp_path):
    repo_root = Path.cwd()
    repo = prepare_repo_copy(tmp_path, repo_root)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    write_fake_bin(fake_bin, "udevadm", udevadm_stub_body())
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
    write_fake_bin(fake_bin, "install", install_stub_body())

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
        "QUEUE_HOST_DIR": str(tmp_path / "queue"),
        "UDEV_RULE_DIR": str(tmp_path / "udev"),
        "SYSTEMD_UNIT_DIR": str(tmp_path / "units"),
        "STATE_DIR": str(state_dir),
        "IMAGE_NAME": "localhost/sms-to-telegram:latest",
    }

    result = subprocess.run(["bash", "setup.sh"], cwd=repo, env=env, capture_output=True, text=True)

    assert result.returncode == 0
    assert "build triggered: source fingerprint changed" in result.stdout
    assert "podman:build" in log.read_text()


def test_setup_installs_modem_reattach_rule_and_unit(tmp_path):
    """A re-enumerated modem is only recovered by a container restart, so the
    udev rule and its oneshot unit must be deployed alongside the quadlet."""
    repo_root = Path.cwd()
    repo = prepare_repo_copy(tmp_path, repo_root)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    write_fake_bin(fake_bin, "udevadm", udevadm_stub_body())
    log = tmp_path / "calls.log"

    write_fake_bin(
        fake_bin,
        "podman",
        "#!/bin/sh\n"
        "echo \"podman:$@\" >> \"$CALLS_LOG\"\n"
        "if [ \"$1\" = image ] && [ \"$2\" = inspect ]; then echo 'sha256:remote-image'; exit 0; fi\n"
        "exit 0\n",
    )
    write_fake_bin(fake_bin, "sudo", "#!/bin/sh\nshift\nexec \"$@\"\n")
    write_fake_bin(fake_bin, "systemctl", "#!/bin/sh\nexit 0\n")
    write_fake_bin(fake_bin, "install", install_stub_body())

    env = os.environ | {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "CALLS_LOG": str(log),
        "QUADLET_DIR": str(tmp_path / "quadlet"),
        "QUEUE_HOST_DIR": str(tmp_path / "queue"),
        "UDEV_RULE_DIR": str(tmp_path / "udev"),
        "SYSTEMD_UNIT_DIR": str(tmp_path / "units"),
        "STATE_DIR": str(repo / ".deploy"),
        "IMAGE_NAME": "ghcr.io/konclave/sms-to-telegram:latest",
    }

    result = subprocess.run(["bash", str(repo / "setup.sh")], cwd=tmp_path, env=env, capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "udev" / "99-sms-modem-reattach.rules").exists()
    assert (tmp_path / "units" / "sms-modem-reattach.service").exists()
    assert "udevadm:control --reload-rules" in log.read_text()


def test_setup_runs_podman_through_sudo(tmp_path):
    """The service runs under root podman, so setup.sh must query and build
    against root storage. Unsudoed podman hits rootless storage: image_id comes
    back empty, and a locally built image is invisible to the service."""
    repo_root = Path.cwd()
    repo = prepare_repo_copy(tmp_path, repo_root)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    write_fake_bin(fake_bin, "udevadm", udevadm_stub_body())
    log = tmp_path / "calls.log"

    write_fake_bin(
        fake_bin,
        "podman",
        "#!/bin/sh\n"
        "if [ \"$1\" = image ] && [ \"$2\" = exists ]; then exit 1; fi\n"
        "if [ \"$1\" = image ] && [ \"$2\" = inspect ]; then echo 'sha256:root-image'; exit 0; fi\n"
        "exit 0\n",
    )
    write_fake_bin(
        fake_bin,
        "sudo",
        "#!/bin/sh\necho \"sudo:$@\" >> \"$CALLS_LOG\"\nshift\nexec \"$@\"\n",
    )
    write_fake_bin(fake_bin, "systemctl", "#!/bin/sh\nexit 0\n")
    write_fake_bin(fake_bin, "install", install_stub_body())

    env = os.environ | {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "CALLS_LOG": str(log),
        "QUADLET_DIR": str(tmp_path / "quadlet"),
        "QUEUE_HOST_DIR": str(tmp_path / "queue"),
        "UDEV_RULE_DIR": str(tmp_path / "udev"),
        "SYSTEMD_UNIT_DIR": str(tmp_path / "units"),
        "STATE_DIR": str(repo / ".deploy"),
        "IMAGE_NAME": "localhost/sms-to-telegram:latest",
    }

    result = subprocess.run(["bash", str(repo / "setup.sh")], cwd=tmp_path, env=env, capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    calls = log.read_text()
    assert "sudo:-- podman image exists localhost/sms-to-telegram:latest" in calls
    assert "sudo:-- podman build" in calls
    assert "sudo:-- podman image inspect" in calls

    state = json.loads((repo / ".deploy" / "sms-to-telegram-state.json").read_text())
    assert state["image_id"] == "sha256:root-image"


def test_setup_installs_modem_check_timer(tmp_path):
    """The checker runs from the repo; only the units are installed, with
    ExecStart pointing back at the checkout so git pull is the update path."""
    repo_root = Path.cwd()
    repo = prepare_repo_copy(tmp_path, repo_root)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    write_fake_bin(fake_bin, "udevadm", udevadm_stub_body())
    log = tmp_path / "calls.log"

    write_fake_bin(
        fake_bin,
        "podman",
        "#!/bin/sh\n"
        "if [ \"$1\" = image ] && [ \"$2\" = inspect ]; then echo 'sha256:x'; exit 0; fi\n"
        "exit 0\n",
    )
    write_fake_bin(fake_bin, "sudo", "#!/bin/sh\nshift\nexec \"$@\"\n")
    write_fake_bin(fake_bin, "systemctl", "#!/bin/sh\necho \"systemctl:$@\" >> \"$CALLS_LOG\"\nexit 0\n")
    write_fake_bin(fake_bin, "install", install_stub_body())

    env = os.environ | {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "CALLS_LOG": str(log),
        "QUADLET_DIR": str(tmp_path / "quadlet"),
        "QUEUE_HOST_DIR": str(tmp_path / "queue"),
        "UDEV_RULE_DIR": str(tmp_path / "udev"),
        "SYSTEMD_UNIT_DIR": str(tmp_path / "units"),
        "STATE_DIR": str(repo / ".deploy"),
        "IMAGE_NAME": "ghcr.io/konclave/sms-to-telegram:latest",
    }

    result = subprocess.run(
        ["bash", str(repo / "setup.sh")], cwd=tmp_path, env=env,
        capture_output=True, text=True,
    )

    assert result.returncode == 0, result.stderr
    service = tmp_path / "units" / "sms-modem-check.service"
    timer = tmp_path / "units" / "sms-modem-check.timer"
    assert service.exists()
    assert timer.exists()

    # ExecStart must point at the checkout, not a copied file.
    assert f"{repo}/host/sms_modem_check.py" in service.read_text()

    calls = log.read_text()
    assert "systemctl:enable --now sms-modem-check.timer" in calls
