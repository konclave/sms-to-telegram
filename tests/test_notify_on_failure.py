"""The quadlet failure notifier sends one Telegram message per failure.

A re-enumerating modem restarts the forwarder about once a minute and ~64% of
those restarts fail, so the notifier turned a single hardware fault into 2396
Telegram messages. These tests pin the throttle that collapses a run of
failures for one unit into a single alert per cooldown window.
"""

import os
import subprocess
from pathlib import Path

SCRIPT = Path("systemd-notify-on-failure.sh").resolve()


def make_stubs(tmp_path: Path) -> Path:
    """Stand in for the host tools the script shells out to."""
    stub_dir = tmp_path / "stubs"
    stub_dir.mkdir()

    # The alert body is multi-line, so flatten each invocation to one record.
    curl = stub_dir / "curl"
    curl.write_text(
        "#!/bin/sh\n"
        'printf \'%s\\n\' "$(printf \'%s\' "$*" | tr \'\\n\' \' \')" >> "$CURL_CALLS"\n'
    )
    curl.chmod(0o755)

    journalctl = stub_dir / "journalctl"
    journalctl.write_text("#!/bin/sh\nprintf 'last log line\\n'\n")
    journalctl.chmod(0o755)

    return stub_dir


def run_notifier(
    tmp_path: Path,
    service: str = "sms-to-telegram.service",
    *,
    cooldown: str = "3600",
) -> subprocess.CompletedProcess[str]:
    env_file = tmp_path / "notify.env"
    if not env_file.exists():
        env_file.write_text('BOT_TOKEN="token"\nCHAT_ID="chat"\n')

    stub_dir = make_stubs(tmp_path) if not (tmp_path / "stubs").exists() else tmp_path / "stubs"

    return subprocess.run(
        ["bash", str(SCRIPT), service],
        env={
            **os.environ,
            "PATH": f"{stub_dir}:/usr/bin:/bin",
            "CURL_CALLS": str(tmp_path / "curl_calls"),
            "NOTIFY_ENV_FILE": str(env_file),
            "NOTIFY_LOG": str(tmp_path / "failures.log"),
            "NOTIFY_STATE_DIR": str(tmp_path / "state"),
            "NOTIFY_COOLDOWN_SECONDS": cooldown,
        },
        capture_output=True,
        text=True,
    )


def sends(tmp_path: Path) -> list[str]:
    calls = tmp_path / "curl_calls"
    if not calls.exists():
        return []
    return [line for line in calls.read_text().splitlines() if line.strip()]


def test_script_is_executable():
    assert SCRIPT.exists()
    assert SCRIPT.stat().st_mode & 0o111


def test_first_failure_sends_an_alert(tmp_path):
    result = run_notifier(tmp_path)

    assert result.returncode == 0, result.stderr
    assert len(sends(tmp_path)) == 1
    assert "sms-to-telegram.service" in sends(tmp_path)[0]


def test_further_failures_inside_the_cooldown_are_not_sent(tmp_path):
    for _ in range(50):
        assert run_notifier(tmp_path).returncode == 0

    assert len(sends(tmp_path)) == 1


def age_last_sent(tmp_path: Path, seconds: int) -> None:
    """Push the last-sent stamp back so the cooldown has provably elapsed.

    Sleeping past a short cooldown races the notifier's whole-second clock;
    rewriting the persisted stamp exercises the same seam deterministically.
    """
    state = next((tmp_path / "state").iterdir())
    last_sent, suppressed, since = state.read_text().split()
    state.write_text(f"{int(last_sent) - seconds} {suppressed} {since}\n")


def test_the_next_alert_reports_how_many_were_suppressed(tmp_path):
    assert run_notifier(tmp_path).returncode == 0
    for _ in range(5):
        assert run_notifier(tmp_path).returncode == 0

    age_last_sent(tmp_path, 3601)
    assert run_notifier(tmp_path).returncode == 0

    calls = sends(tmp_path)
    assert len(calls) == 2
    assert "5 further failures suppressed" in calls[1]


def test_a_quiet_period_resets_the_suppressed_counter(tmp_path):
    run_notifier(tmp_path)
    for _ in range(3):
        run_notifier(tmp_path)

    age_last_sent(tmp_path, 3601)
    run_notifier(tmp_path)  # reports the 3, clears the counter

    age_last_sent(tmp_path, 3601)
    run_notifier(tmp_path)

    calls = sends(tmp_path)
    assert len(calls) == 3
    assert "3 further failures suppressed" in calls[1]
    assert "suppressed" not in calls[2]


def test_units_are_throttled_independently(tmp_path):
    assert run_notifier(tmp_path, "sms-to-telegram.service").returncode == 0
    assert run_notifier(tmp_path, "zigbee2mqtt.service").returncode == 0

    calls = sends(tmp_path)
    assert len(calls) == 2
    assert any("zigbee2mqtt.service" in call for call in calls)


def test_every_failure_is_still_recorded_locally(tmp_path):
    """Throttling the Telegram send must not lose the local audit trail."""
    for _ in range(10):
        run_notifier(tmp_path)

    log = (tmp_path / "failures.log").read_text()
    assert log.count("FAILED: sms-to-telegram.service") == 10


def test_a_malformed_state_file_does_not_wedge_alerting(tmp_path):
    run_notifier(tmp_path)
    state = next((tmp_path / "state").iterdir())
    state.write_text("garbage not an epoch\n")

    assert run_notifier(tmp_path).returncode == 0
    assert len(sends(tmp_path)) == 2


def test_a_unit_name_cannot_escape_the_state_directory(tmp_path):
    assert run_notifier(tmp_path, "../../etc/passwd").returncode == 0

    written = list((tmp_path / "state").iterdir())
    assert len(written) == 1
    assert "/" not in written[0].name


def test_setup_installs_the_notifier(tmp_path):
    setup = Path("setup.sh").read_text()

    assert "systemd-notify-on-failure.sh" in setup
    assert "/usr/local/lib/systemd-notify" in setup
    assert "$NOTIFY_DIR/on-failure.sh" in setup
