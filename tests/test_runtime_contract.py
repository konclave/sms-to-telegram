from pathlib import Path


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
    pyproject = Path("pyproject.toml").read_text()

    assert 'requires-python = ">=3.14,<3.15"' in pyproject
    assert "[project.optional-dependencies]" in pyproject
    assert 'dev = ["pytest==8.4.1"]' in pyproject
    assert '[project.scripts]' in pyproject
    assert 'sms-forwarder-enqueue = "sms_forwarder.enqueue_hook:main"' in pyproject
    assert 'sms-forwarder-worker = "sms_forwarder.worker:main"' in pyproject
    assert 'sms-forwarder-healthcheck = "sms_forwarder.healthcheck:main"' in pyproject
    assert Path(".python-version").read_text().strip() == "3.14"
    assert Path("uv.lock").exists()
    assert not Path("requirements-dev.txt").exists()
