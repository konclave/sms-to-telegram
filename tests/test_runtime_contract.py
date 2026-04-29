from pathlib import Path
import shutil
import subprocess
import tomllib
import uuid

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


def test_repo_uses_uv_packaging_metadata():
    pyproject = tomllib.loads(Path("pyproject.toml").read_text())

    assert pyproject["build-system"] == {
        "requires": ["setuptools>=80"],
        "build-backend": "setuptools.build_meta",
    }
    assert pyproject["project"]["requires-python"] == ">=3.14,<3.15"
    assert pyproject["project"]["optional-dependencies"]["dev"] == ["pytest==8.4.1"]
    assert pyproject["project"]["scripts"] == {
        "sms-forwarder-enqueue": "sms_forwarder.enqueue_hook:main",
        "sms-forwarder-worker": "sms_forwarder.worker:main",
        "sms-forwarder-healthcheck": "sms_forwarder.healthcheck:main",
    }
    assert pyproject["dependency-groups"]["dev"] == ["pytest==8.4.1"]
    assert Path(".python-version").read_text().strip() == "3.14"
    assert Path("uv.lock").exists()
    assert not Path("requirements-dev.txt").exists()


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
