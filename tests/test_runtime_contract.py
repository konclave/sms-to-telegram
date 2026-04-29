from pathlib import Path
import tomllib


def test_runtime_files_reference_python_forwarder():
    assert "RunOnReceive=/usr/bin/enqueue_sms.py" in Path("gammurc").read_text()
    entrypoint = Path("entrypoint.sh").read_text()
    assert "/usr/bin/send_worker.py &" in entrypoint
    assert "WORKER_PID_FILE=${WORKER_PID_FILE:-/var/run/sms-forwarder-worker.pid}" in entrypoint
    quadlet = Path("sms-to-telegram.container").read_text()
    assert "Image=localhost/sms-to-telegram:latest" in quadlet


def test_readme_documents_local_quadlet_deploy_tracking():
    readme = Path("README.md").read_text()
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
