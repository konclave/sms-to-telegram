import shutil
import subprocess
import time
import uuid
from pathlib import Path

import pytest

# entrypoint.sh runs as the container's PID 1. The kernel discards signals that
# PID 1 has no handler installed for, so without an explicit trap SIGTERM is
# ignored, podman waits out its stop timeout and resorts to SIGKILL. systemd
# then records status=137/n/a, marks the unit failed and fires OnFailure -- so
# every deliberate restart pages the operator as if the service had crashed.
STUB_AND_EXEC = r"""
set -eu
mkdir -p /stub /var/run
printf '%s' "$ENTRYPOINT_SRC" > /entrypoint.sh
for name in sms-forwarder-worker gammu-smsd sms-forwarder-modem-monitor; do
  printf '#!/bin/sh\ntrap "exit 0" TERM\nwhile :; do sleep 1; done\n' > "/stub/$name"
  chmod +x "/stub/$name"
done
printf '#!/bin/sh\ncat\n' > /stub/envsubst
chmod +x /stub/envsubst
: > /etc/gammurc
PATH=/stub:$PATH
export PATH
exec /bin/sh /entrypoint.sh
"""


def container_engine() -> str | None:
    for candidate in ("podman", "docker"):
        engine = shutil.which(candidate)
        if engine is None:
            continue
        if subprocess.run((engine, "info"), capture_output=True).returncode == 0:
            return engine
    return None


def test_entrypoint_installs_a_termination_trap():
    entrypoint = Path("entrypoint.sh").read_text()

    assert "trap" in entrypoint
    assert "TERM" in entrypoint


def test_entrypoint_stops_promptly_and_cleanly_when_signalled_as_pid1():
    engine = container_engine()
    if engine is None:
        pytest.skip("podman or docker is required to run entrypoint.sh as PID 1")

    name = f"sms-entrypoint-sigterm-{uuid.uuid4().hex[:8]}"
    # Passed through the environment rather than bind-mounted so the test does
    # not depend on SELinux relabelling or the file carrying an exec bit.
    entrypoint = Path("entrypoint.sh").read_text()
    stop_timeout = 10

    subprocess.run(
        (
            engine, "run", "-d", "--name", name,
            "-e", f"ENTRYPOINT_SRC={entrypoint}",
            "docker.io/library/alpine:3", "/bin/sh", "-c", STUB_AND_EXEC,
        ),
        check=True, text=True, capture_output=True,
    )
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            probe = subprocess.run(
                (engine, "exec", name, "pgrep", "-f", "sms-forwarder-modem-monitor"),
                capture_output=True,
            )
            if probe.returncode == 0:
                break
            time.sleep(0.5)
        else:
            pytest.fail("entrypoint.sh never reached its supervision loop")

        started = time.monotonic()
        subprocess.run(
            (engine, "stop", "-t", str(stop_timeout), name),
            check=True, text=True, capture_output=True,
        )
        elapsed = time.monotonic() - started

        exit_code = subprocess.run(
            (engine, "inspect", "-f", "{{.State.ExitCode}}", name),
            check=True, text=True, capture_output=True,
        ).stdout.strip()
    finally:
        subprocess.run((engine, "rm", "-f", name), capture_output=True)

    assert elapsed < stop_timeout / 2, (
        f"SIGTERM was ignored: stop took {elapsed:.1f}s and needed SIGKILL"
    )
    assert exit_code == "0", (
        f"a clean stop must not look like a crash to systemd, got exit code {exit_code}"
    )
