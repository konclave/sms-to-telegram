from pathlib import Path


def test_runtime_files_reference_python_forwarder():
    assert "RunOnReceive=/usr/bin/enqueue_sms.py" in Path("gammurc").read_text()
    entrypoint = Path("entrypoint.sh").read_text()
    assert "/usr/bin/send_worker.py &" in entrypoint
    assert "WORKER_PID_FILE=${WORKER_PID_FILE:-/var/run/sms-forwarder-worker.pid}" in entrypoint
