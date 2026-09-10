import subprocess
import threading
import time
from pathlib import Path

import pytest

HELPER = Path("wait-for-modem-device.sh").resolve()


def run_helper(
    device: Path,
    *,
    stable_seconds: str = "2",
    timeout: str = "5",
    poll_interval: str = "0.1",
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sh", str(HELPER)],
        env={
            "PATH": "/usr/bin:/bin",
            "MODEM_DEVICE": str(device),
            "MODEM_STABLE_SECONDS": stable_seconds,
            "MODEM_WAIT_TIMEOUT": timeout,
            "MODEM_POLL_INTERVAL": poll_interval,
        },
        capture_output=True,
        text=True,
    )


def test_helper_is_executable():
    assert HELPER.exists()
    assert HELPER.stat().st_mode & 0o111


def test_succeeds_when_device_is_already_present_and_stays(tmp_path):
    device = tmp_path / "modem"
    device.touch()

    result = run_helper(device)

    assert result.returncode == 0, result.stderr


def test_succeeds_once_a_late_device_settles(tmp_path):
    device = tmp_path / "modem"

    def appear() -> None:
        time.sleep(0.5)
        device.touch()

    thread = threading.Thread(target=appear)
    thread.start()
    try:
        result = run_helper(device, timeout="8")
    finally:
        thread.join()

    assert result.returncode == 0, result.stderr


def test_fails_when_the_device_never_appears(tmp_path):
    """A restart issued with no device makes podman fail on AddDevice, which
    systemd reports as a unit failure and the notifier turns into an alert.
    Failing here instead keeps the restart -- and the alert -- from happening."""
    result = run_helper(tmp_path / "absent", timeout="1")

    assert result.returncode != 0


def test_does_not_succeed_while_the_device_is_flapping(tmp_path):
    """The modem re-enumerates several times in a burst. Restarting on the
    first reappearance races the next disconnect, so the device must hold
    still for the full stability window before the restart is allowed."""
    device = tmp_path / "modem"
    stop = threading.Event()

    def flap() -> None:
        while not stop.is_set():
            device.touch()
            time.sleep(0.3)
            device.unlink(missing_ok=True)
            time.sleep(0.3)

    thread = threading.Thread(target=flap)
    thread.start()
    try:
        result = run_helper(device, stable_seconds="2", timeout="3")
    finally:
        stop.set()
        thread.join()

    assert result.returncode != 0


def test_reattach_unit_waits_for_a_stable_device_instead_of_sleeping(tmp_path):
    unit = Path("sms-modem-reattach.service").read_text()

    assert "ExecStartPre=/usr/bin/sleep 5" not in unit
    assert "/usr/local/lib/sms-to-telegram/wait-for-modem-device.sh" in unit
    assert "ExecStart=/usr/bin/systemctl restart sms-to-telegram.service" in unit
