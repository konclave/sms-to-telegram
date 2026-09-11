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
#
# A trap alone is not enough: the stop is only clean if PID 1 finishes within
# the stop timeout, which it cannot do while waiting on a child that never
# acts on the signal.
RESPONSIVE_CHILD = '#!/bin/sh\\ntrap "exit 0" TERM\\nwhile :; do sleep 1; done\\n'
# gammu-smsd blocked on a dead USB tty does not act on SIGTERM. The modem
# re-enumerates constantly, so this is the state a restart usually finds it in.
WEDGED_CHILD = '#!/bin/sh\\ntrap "" TERM\\nwhile :; do sleep 1; done\\n'


def stub_and_exec(gammu_body: str) -> str:
    return rf"""
set -eu
mkdir -p /stub /var/run
printf '%s' "$ENTRYPOINT_SRC" > /entrypoint.sh
for name in sms-forwarder-worker sms-forwarder-modem-monitor; do
  printf '{RESPONSIVE_CHILD}' > "/stub/$name"
  chmod +x "/stub/$name"
done
printf '{gammu_body}' > /stub/gammu-smsd
chmod +x /stub/gammu-smsd
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


def stop_entrypoint_as_pid1(engine: str, gammu_body: str, stop_timeout: int) -> tuple[float, str]:
    """Run entrypoint.sh as a container's PID 1, stop it, and report how long
    the stop took and what exit code the container reported."""
    name = f"sms-entrypoint-sigterm-{uuid.uuid4().hex[:8]}"
    # Passed through the environment rather than bind-mounted so the test does
    # not depend on SELinux relabelling or the file carrying an exec bit.
    entrypoint = Path("entrypoint.sh").read_text()

    subprocess.run(
        (
            engine, "run", "-d", "--name", name,
            "-e", f"ENTRYPOINT_SRC={entrypoint}",
            "docker.io/library/alpine:3", "/bin/sh", "-c", stub_and_exec(gammu_body),
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

    return elapsed, exit_code


def test_entrypoint_installs_a_termination_trap():
    entrypoint = Path("entrypoint.sh").read_text()

    assert "trap" in entrypoint
    assert "TERM" in entrypoint


def test_entrypoint_teardown_cannot_block_forever():
    """The teardown must bound how long it waits on the children. Waiting
    without a bound is what let a wedged gammu-smsd hold the stop open past
    podman's timeout even with the trap in place."""
    entrypoint = Path("entrypoint.sh").read_text()

    assert "kill -KILL" in entrypoint


def test_entrypoint_stops_promptly_and_cleanly_when_signalled_as_pid1():
    engine = container_engine()
    if engine is None:
        pytest.skip("podman or docker is required to run entrypoint.sh as PID 1")

    stop_timeout = 10
    elapsed, exit_code = stop_entrypoint_as_pid1(engine, RESPONSIVE_CHILD, stop_timeout)

    assert elapsed < stop_timeout / 2, (
        f"SIGTERM was ignored: stop took {elapsed:.1f}s and needed SIGKILL"
    )
    assert exit_code == "0", (
        f"a clean stop must not look like a crash to systemd, got exit code {exit_code}"
    )


def test_entrypoint_stops_cleanly_even_when_a_child_ignores_sigterm():
    """Observed in production on v1.3.0: 8 of 39 restarts still exited 137
    because the teardown waited on a gammu-smsd that was blocked on a dead USB
    tty. PID 1 must give up on it and exit on its own terms."""
    engine = container_engine()
    if engine is None:
        pytest.skip("podman or docker is required to run entrypoint.sh as PID 1")

    stop_timeout = 10
    elapsed, exit_code = stop_entrypoint_as_pid1(engine, WEDGED_CHILD, stop_timeout)

    assert elapsed < stop_timeout, (
        f"a child ignoring SIGTERM held the stop open for {elapsed:.1f}s "
        "until podman resorted to SIGKILL"
    )
    assert exit_code == "0", (
        f"a clean stop must not look like a crash to systemd, got exit code {exit_code}"
    )
