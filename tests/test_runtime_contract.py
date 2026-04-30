import os
from pathlib import Path
import re
import shutil
import subprocess
import tomllib
import uuid
import zipfile

import pytest


def test_runtime_files_reference_installed_forwarder_commands():
    assert "RunOnReceive=sms-forwarder-enqueue" in Path("gammurc").read_text()
    entrypoint = Path("entrypoint.sh").read_text()
    assert "sms-forwarder-worker &" in entrypoint
    assert "WORKER_PID_FILE=${WORKER_PID_FILE:-/var/run/sms-forwarder-worker.pid}" in entrypoint
    quadlet = Path("sms-to-telegram.container").read_text()
    assert "Image=localhost/sms-to-telegram:latest" in quadlet


def test_readme_documents_local_quadlet_deploy_tracking():
    readme = Path("README.md").read_text()
    assert "uv sync" in readme
    assert "uv run pytest" in readme
    assert "sms-forwarder-enqueue" in readme
    assert "sms-forwarder-worker" in readme
    assert "Python 3.14" in readme
    assert "localhost/sms-to-telegram:latest" in readme
    assert ".deploy/sms-to-telegram-state.json" in readme
    assert "build skipped: fingerprint unchanged" in readme


def test_repo_defines_ghcr_publish_workflow_contract():
    workflow = Path(".github/workflows/publish-ghcr.yml").read_text()

    assert "v*" in workflow
    assert "ghcr.io" in workflow
    assert '${GITHUB_REPOSITORY_OWNER,,}' in workflow
    assert "Dockerfile.alpine" in workflow
    assert "packages: write" in workflow
    assert "docker/setup-buildx-action" in workflow
    assert "docker/login-action" in workflow
    assert "docker/build-push-action" in workflow
    assert "VERSION_PATTERN='^v[0-9]+\\.[0-9]+\\.[0-9]+$'" in workflow
    assert 'grep -Eq "${VERSION_PATTERN}"' in workflow
    assert "Expected a semantic version tag like v1.2.3" in workflow
    assert "${{ env.IMAGE_NAME }}:${{ env.VERSION_TAG }}" in workflow
    assert "${{ env.IMAGE_NAME }}:${{ env.NORMALIZED_TAG }}" in workflow
    assert "${{ env.IMAGE_NAME }}:latest" not in workflow


def test_readme_documents_ghcr_release_workflow():
    readme = Path("README.md").read_text()

    assert "GitHub Actions" in readme
    assert "ghcr.io/<owner>/sms-to-telegram" in readme
    assert "Dockerfile.alpine" in readme
    assert "v1.2.3" in readme
    assert "version tags" in readme


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
    assert re.fullmatch(r"\d+\.\d+\.\d+\.dev\d+(?:\+[a-z0-9.]+)?", resolved)


def test_container_files_target_python_314_and_uv_installation():
    primary = Path("Dockerfile").read_text()
    alpine = Path("Dockerfile.alpine").read_text()

    assert "FROM python:3.14" in primary
    assert "FROM python:3.14" in alpine
    assert "uv" in primary
    assert "uv" in alpine
    assert "sms-forwarder-healthcheck" in primary
    assert "sms-forwarder-healthcheck" in alpine


def _container_engine() -> str | None:
    for candidate in ("podman", "docker"):
        engine = shutil.which(candidate)
        if engine is None:
            continue
        probe = subprocess.run(
            (engine, "info"),
            text=True,
            capture_output=True,
        )
        if probe.returncode == 0:
            return engine
    return None


def _run(
    *args: str,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        check=check,
        text=True,
        capture_output=True,
    )


@pytest.mark.parametrize(
    ("dockerfile", "tag_suffix"),
    [
        ("Dockerfile", "debian"),
        ("Dockerfile.alpine", "alpine"),
    ],
)
def test_container_images_expose_python_uv_and_packaged_console_scripts(
    dockerfile: str,
    tag_suffix: str,
):
    engine = _container_engine()
    if engine is None:
        pytest.skip("podman or docker is required for container runtime contract checks")

    tag = f"sms-to-telegram-runtime-contract-{tag_suffix}-{uuid.uuid4().hex[:8]}"
    try:
        _run(engine, "build", "-f", dockerfile, "-t", tag, ".")
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
                "command -v sms-forwarder-healthcheck"
            ),
        )
    finally:
        _run(engine, "image", "rm", "-f", tag, check=False)

    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    assert lines[0].startswith("Python 3.14"), result.stdout
    assert lines[1].startswith("uv "), result.stdout
    assert lines[2].endswith("sms-forwarder-enqueue"), result.stdout
    assert lines[3].endswith("sms-forwarder-worker"), result.stdout
    assert lines[4].endswith("sms-forwarder-healthcheck"), result.stdout
