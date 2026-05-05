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

_FULL_OUTPUT = """\
Name               : HUAWEI Mobile
Manufacturer       : Huawei
Model              : E173
Signal strength    : -89 dBm (34 %)
Network            : O2 - CZ (home, UMTS)
Charge state       : not connected to charger
Battery level      : 0 %
"""


# ---------------------------------------------------------------------------
# Parse tests
# ---------------------------------------------------------------------------


def test_parse_full_output_extracts_signal_and_network():
    s = parse_monitor_output(_FULL_OUTPUT)
    assert s.signal_percent == 34
    assert s.network_state == "home, umts"


def test_parse_output_searching():
    text = "Network            : (searching)\n"
    s = parse_monitor_output(text)
    assert s.network_state == "searching"


def test_parse_output_not_registered():
    text = "Network            : (not registered)\n"
    s = parse_monitor_output(text)
    assert s.network_state == "not registered"


def test_parse_output_roaming():
    text = "Signal strength    : -70 dBm (60 %)\nNetwork            : Vodafone (roaming, GPRS)\n"
    s = parse_monitor_output(text)
    assert s.signal_percent == 60
    assert s.network_state == "roaming, gprs"


def test_parse_missing_signal_line():
    text = "Network            : O2 (home, GPRS)\n"
    s = parse_monitor_output(text)
    assert s.signal_percent is None
    assert s.network_state == "home, gprs"


def test_parse_missing_network_line():
    text = "Signal strength    : -67 dBm (73 %)\n"
    s = parse_monitor_output(text)
    assert s.signal_percent == 73
    assert s.network_state is None


def test_parse_empty_output():
    s = parse_monitor_output("")
    assert s == ModemStatus(signal_percent=None, network_state=None)


def test_parse_malformed_signal_no_percent():
    text = "Signal strength    : unknown\n"
    s = parse_monitor_output(text)
    assert s.signal_percent is None


# ---------------------------------------------------------------------------
# run_monitor subprocess tests
# ---------------------------------------------------------------------------


def test_run_monitor_returns_status_on_success():
    proc = MagicMock()
    proc.returncode = 0
    proc.stdout = _FULL_OUTPUT
    with patch("sms_forwarder.modem_monitor.subprocess.run", return_value=proc):
        status, err = run_monitor("/etc/gammurc")
    assert err is None
    assert status is not None
    assert status.signal_percent == 34


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


def _good(signal: int = 50, state: str = "home") -> ModemStatus:
    return ModemStatus(signal_percent=signal, network_state=state)


def _lost(state: str = "searching") -> ModemStatus:
    return ModemStatus(signal_percent=50, network_state=state)


def _run_iterations(
    statuses: list[ModemStatus | None],
    *,
    signal_threshold: int = 20,
    chat_id: str = "123",
) -> list[str]:
    """Drive the monitor state machine for the given sequence of statuses.

    Each item is either a ModemStatus (tool success) or None (tool error).
    Returns the list of alert texts sent.
    """
    from sms_forwarder import modem_monitor as mm

    alerts: list[str] = []
    client = _make_client(alerts)

    connection_alert_active = False
    signal_alert_active = False
    error_alert_active = False

    for status in statuses:
        if status is None:
            if not error_alert_active:
                mm._send_alert(client, chat_id, "Modem: monitor tool failing")
                error_alert_active = True
            continue

        if error_alert_active:
            mm._send_alert(client, chat_id, "Modem: monitor tool recovered")
            error_alert_active = False

        connection_lost = status.network_state in mm._LOST_STATES or status.network_state is None
        if connection_lost and not connection_alert_active:
            mm._send_alert(client, chat_id, f"Modem: network connection lost (state: {status.network_state or 'unknown'})")
            connection_alert_active = True
        elif not connection_lost and connection_alert_active:
            mm._send_alert(client, chat_id, f"Modem: network connection restored (state: {status.network_state})")
            connection_alert_active = False

        signal_low = status.signal_percent is not None and status.signal_percent < signal_threshold
        if signal_low and not signal_alert_active:
            mm._send_alert(client, chat_id, f"Modem: signal low ({status.signal_percent}% — below threshold {signal_threshold}%)")
            signal_alert_active = True
        elif not signal_low and signal_alert_active and status.signal_percent is not None:
            mm._send_alert(client, chat_id, f"Modem: signal recovered ({status.signal_percent}%)")
            signal_alert_active = False

    return alerts


def test_no_alert_on_first_good_reading():
    alerts = _run_iterations([_good()])
    assert alerts == []


def test_connection_lost_sends_one_alert():
    alerts = _run_iterations([_lost(), _lost()])
    assert len(alerts) == 1
    assert "connection lost" in alerts[0]


def test_connection_recovered_sends_recovery_alert():
    alerts = _run_iterations([_lost(), _good()])
    assert len(alerts) == 2
    assert "connection lost" in alerts[0]
    assert "connection restored" in alerts[1]


def test_not_registered_triggers_connection_lost():
    alerts = _run_iterations([_lost("not registered")])
    assert len(alerts) == 1
    assert "not registered" in alerts[0]


def test_signal_low_sends_one_alert():
    alerts = _run_iterations(
        [ModemStatus(10, "home"), ModemStatus(10, "home")],
        signal_threshold=20,
    )
    assert len(alerts) == 1
    assert "signal low" in alerts[0]


def test_signal_recovered_sends_recovery_alert():
    alerts = _run_iterations(
        [ModemStatus(10, "home"), ModemStatus(50, "home")],
        signal_threshold=20,
    )
    assert len(alerts) == 2
    assert "signal low" in alerts[0]
    assert "signal recovered" in alerts[1]


def test_no_spurious_recovery_when_signal_line_absent_and_no_prior_alert():
    alerts = _run_iterations([ModemStatus(None, "home")])
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
        [ModemStatus(10, "home"), None],
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
