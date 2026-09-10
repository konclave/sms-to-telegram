from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from sms_forwarder.modem_monitor import (
    ModemStatus,
    _send_alert,
    main,
    parse_monitor_output,
    run_monitor,
)
from sms_forwarder.telegram_api import RetryableDeliveryError, TerminalDeliveryError

# Captured verbatim from `gammu-smsd-monitor -c /etc/gammurc -n 1 -d 1`
# running against the deployed image on the host.
_REAL_OUTPUT = """\
gammu-smsd-monitor[196]: Mapped POSIX RO shared memory at 0x7f8b2982e000
Client: Gammu 1.43.2 on Linux, kernel 6.19.11-200.fc43.x86_64 compiler GCC 15.2
PhoneID: 
IMEI: 358192014259454
IMSI: 250016504607187
Sent: 0
Received: 0
Failed: 0
BatterPercent: 0
NetworkSignal: 33
"""

# gammu-smsd publishes empty identity fields when it has not reached the phone.
_UNREACHABLE_OUTPUT = """\
Client: Gammu 1.43.2 on Linux
PhoneID: 
IMEI: 
IMSI: 
Sent: 0
Received: 0
Failed: 0
BatterPercent: 0
NetworkSignal: 0
"""


# ---------------------------------------------------------------------------
# Parse tests
# ---------------------------------------------------------------------------


def test_parse_reads_signal_from_network_signal_field():
    """gammu-smsd-monitor reports `NetworkSignal: N`, not the `Signal strength`
    line that `gammu --monitor` prints."""
    s = parse_monitor_output(_REAL_OUTPUT)
    assert s.signal_percent == 33


def test_parse_reads_imei():
    s = parse_monitor_output(_REAL_OUTPUT)
    assert s.imei == "358192014259454"


def test_parse_treats_populated_imei_as_reachable():
    assert parse_monitor_output(_REAL_OUTPUT).reachable is True


def test_parse_treats_empty_imei_as_unreachable():
    """An empty IMEI means gammu-smsd never got an answer from the modem."""
    s = parse_monitor_output(_UNREACHABLE_OUTPUT)
    assert s.imei is None
    assert s.reachable is False


def test_parse_reads_zero_signal():
    assert parse_monitor_output(_UNREACHABLE_OUTPUT).signal_percent == 0


def test_parse_empty_output_is_unreachable():
    s = parse_monitor_output("")
    assert s == ModemStatus(signal_percent=None, imei=None)
    assert s.reachable is False


def test_parse_ignores_network_signal_when_naming_a_network_state():
    """`NetworkSignal:` must not be mistaken for a network-state field."""
    s = parse_monitor_output(_REAL_OUTPUT)
    assert not hasattr(s, "network_state")


# run_monitor subprocess tests
# ---------------------------------------------------------------------------


def test_run_monitor_returns_status_on_success():
    proc = MagicMock()
    proc.returncode = 0
    proc.stdout = _REAL_OUTPUT
    with patch("sms_forwarder.modem_monitor.subprocess.run", return_value=proc):
        status, err = run_monitor("/etc/gammurc")
    assert err is None
    assert status is not None
    assert status.signal_percent == 33


def test_run_monitor_bounds_the_monitor_to_a_single_loop():
    """gammu-smsd-monitor loops until interrupted unless given --loops.

    Without a loop bound every invocation runs until the 30s timeout kills it,
    so run_monitor never returns a status and the modem is never actually
    monitored.
    """
    proc = MagicMock()
    proc.returncode = 0
    proc.stdout = _REAL_OUTPUT
    with patch("sms_forwarder.modem_monitor.subprocess.run", return_value=proc) as run:
        run_monitor("/etc/gammurc")

    argv = run.call_args.args[0]
    assert "-n" in argv, f"invocation is unbounded and will hit the timeout: {argv}"
    assert argv[argv.index("-n") + 1] == "1"


def test_run_monitor_returns_error_on_nonzero_exit():
    proc = MagicMock()
    proc.returncode = 1
    proc.stderr = "device busy"
    with patch("sms_forwarder.modem_monitor.subprocess.run", return_value=proc):
        status, err = run_monitor("/etc/gammurc")
    assert status is None
    assert "exit_code=1" in err


def test_run_monitor_returns_error_on_timeout():
    with patch(
        "sms_forwarder.modem_monitor.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="gammu-smsd-monitor", timeout=30),
    ):
        status, err = run_monitor("/etc/gammurc")
    assert status is None
    assert "timed out" in err


def test_run_monitor_returns_error_on_file_not_found():
    with patch(
        "sms_forwarder.modem_monitor.subprocess.run",
        side_effect=FileNotFoundError("No such file"),
    ):
        status, err = run_monitor("/etc/gammurc")
    assert status is None
    assert "not found" in err


# ---------------------------------------------------------------------------
# State transition tests
# ---------------------------------------------------------------------------


def _make_client(alerts: list[str]) -> MagicMock:
    client = MagicMock()
    client.send_plain.side_effect = lambda chat_id, text: alerts.append(text)
    return client


_IMEI = "358192014259454"


def _good(signal: int = 50, imei: str = _IMEI) -> ModemStatus:
    return ModemStatus(signal_percent=signal, imei=imei)


def _lost(signal: int = 0) -> ModemStatus:
    """gammu-smsd reachable, but it has no identity for the modem."""
    return ModemStatus(signal_percent=signal, imei=None)


def _run_iterations(
    statuses: list[ModemStatus | None],
    *,
    signal_threshold: int = 20,
) -> list[str]:
    """Drive the real AlertState over a sequence of polls.

    Each item is a ModemStatus (tool success) or None (tool error). Returns the
    alert texts production would send.
    """
    from sms_forwarder import modem_monitor as mm

    state = mm.AlertState(signal_threshold=signal_threshold)
    alerts: list[str] = []
    for status in statuses:
        error_msg = None if status is not None else "gammu-smsd-monitor timed out after 30s"
        alerts.extend(state.evaluate(status, error_msg))
    return alerts


def test_no_alert_on_first_good_reading():
    alerts = _run_iterations([_good()])
    assert alerts == []


def test_connection_lost_sends_one_alert():
    alerts = _run_iterations([_lost(), _lost()])
    assert len(alerts) == 1
    assert "unreachable" in alerts[0]


def test_connection_recovered_sends_recovery_alert():
    alerts = _run_iterations([_lost(), _good()])
    assert len(alerts) == 2
    assert "unreachable" in alerts[0]
    assert "reachable again" in alerts[1]


def test_empty_imei_triggers_unreachable_alert():
    """gammu-smsd-monitor exposes no registration state, so an empty IMEI is
    the only signal that the daemon has not reached the modem."""
    alerts = _run_iterations([_lost()])
    assert len(alerts) == 1
    assert "unreachable" in alerts[0]


def test_signal_low_sends_one_alert():
    alerts = _run_iterations(
        [ModemStatus(10, _IMEI), ModemStatus(10, _IMEI)],
        signal_threshold=20,
    )
    assert len(alerts) == 1
    assert "signal low" in alerts[0]


def test_signal_recovered_sends_recovery_alert():
    alerts = _run_iterations(
        [ModemStatus(10, _IMEI), ModemStatus(50, _IMEI)],
        signal_threshold=20,
    )
    assert len(alerts) == 2
    assert "signal low" in alerts[0]
    assert "signal recovered" in alerts[1]


def test_no_spurious_recovery_when_signal_line_absent_and_no_prior_alert():
    alerts = _run_iterations([ModemStatus(None, _IMEI)])
    assert alerts == []


def test_error_sends_one_alert_then_backs_off():
    alerts = _run_iterations([None, None, None])
    assert len(alerts) == 1
    assert "tool failing" in alerts[0]


def test_error_recovery_sends_recovery_alert():
    alerts = _run_iterations([None, _good()])
    assert len(alerts) == 2
    assert "tool failing" in alerts[0]
    assert "tool recovered" in alerts[1]


def test_signal_state_held_during_tool_errors():
    # Signal was low, then tool fails — no spurious recovery
    alerts = _run_iterations(
        [ModemStatus(10, _IMEI), None],
        signal_threshold=20,
    )
    # First alert: signal low; second: tool failing — NO signal recovery
    assert len(alerts) == 2
    assert "signal low" in alerts[0]
    assert "tool failing" in alerts[1]
    assert not any("signal recovered" in a for a in alerts)


# ---------------------------------------------------------------------------
# main() entry point
# ---------------------------------------------------------------------------


def test_main_raises_on_missing_bot_token(monkeypatch):
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    with pytest.raises(KeyError):
        main()


def test_main_raises_on_missing_chat_id(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "dummy")
    monkeypatch.delenv("CHAT_ID", raising=False)
    with pytest.raises(KeyError):
        main()
