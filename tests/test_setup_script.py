import json
import os
import subprocess
from pathlib import Path


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

    initial = fingerprint()
    (repo / "pyproject.toml").write_text((repo / "pyproject.toml").read_text() + "\n# packaging change\n")
    after_pyproject = fingerprint()
    (repo / "uv.lock").write_text((repo / "uv.lock").read_text() + "\n# lockfile change\n")
    after_uv_lock = fingerprint()
    (repo / "Dockerfile.alpine").write_text((repo / "Dockerfile.alpine").read_text() + "\n# alternate image change\n")
    after_alpine = fingerprint()
    (repo / "entrypoint.sh").write_text((repo / "entrypoint.sh").read_text() + "\n# runtime change\n")
    after_runtime = fingerprint()
    subprocess.run(["git", "tag", "v0.1.0"], cwd=repo, check=True)
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
        "STATE_DIR": str(state_dir),
        "IMAGE_NAME": "localhost/sms-to-telegram:latest",
    }

    result = subprocess.run(["bash", "setup.sh"], cwd=repo, env=env, capture_output=True, text=True)

    assert result.returncode == 0
    assert "build skipped: fingerprint unchanged" in result.stdout
    assert "podman:build" not in log.read_text()
    updated_state = json.loads((state_dir / "sms-to-telegram-state.json").read_text())
    assert updated_state["last_built_at"] == "2026-04-29T18:00:00+00:00"


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
        "STATE_DIR": str(state_dir),
        "IMAGE_NAME": "localhost/sms-to-telegram:latest",
    }

    result = subprocess.run(["bash", "setup.sh"], cwd=repo, env=env, capture_output=True, text=True)

    assert result.returncode == 0
    assert "build triggered: source fingerprint changed" in result.stdout
    assert "podman:build" in log.read_text()
