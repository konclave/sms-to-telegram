from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import dataclass

from sms_forwarder.telegram_api import (
    RetryableDeliveryError,
    TelegramClient,
    TerminalDeliveryError,
)

_SIGNAL_RE = re.compile(r"Signal strength\s*:\s*-?\d+ dBm \((\d+) %\)")
_NETWORK_RE = re.compile(r"Network\s*:\s*.+\(([^)]+)\)")

_LOST_STATES = {"searching", "not registered"}


@dataclass
class ModemStatus:
    signal_percent: int | None
    network_state: str | None


def parse_monitor_output(text: str) -> ModemStatus:
    signal_percent: int | None = None
    network_state: str | None = None
    for line in text.splitlines():
        if signal_percent is None:
            m = _SIGNAL_RE.search(line)
            if m:
                try:
                    signal_percent = int(m.group(1))
                except ValueError:
                    pass
        if network_state is None:
            m = _NETWORK_RE.search(line)
            if m:
                network_state = m.group(1).strip().lower()
    return ModemStatus(signal_percent=signal_percent, network_state=network_state)


def run_monitor(gammu_config: str) -> tuple[ModemStatus | None, str | None]:
    try:
        result = subprocess.run(
            ["gammu-smsd-monitor", "-c", gammu_config],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return None, "gammu-smsd-monitor timed out after 30s"
    except FileNotFoundError:
        return None, "gammu-smsd-monitor not found"
    except OSError as exc:
        return None, f"gammu-smsd-monitor OS error: {exc}"

    if result.returncode != 0:
        stderr = result.stderr.strip()[:200]
        return None, f"exit_code={result.returncode} stderr={stderr!r}"

    try:
        return parse_monitor_output(result.stdout), None
    except Exception as exc:
        return None, f"parse error: {exc}"


def _send_alert(client: TelegramClient, chat_id: str, text: str) -> None:
    try:
        client.send_plain(chat_id, text)
    except RetryableDeliveryError as exc:
        print(f"event=monitor_alert_failed error={exc!r}")
    # TerminalDeliveryError propagates — kills the process intentionally


def main() -> None:
    bot_token = os.environ["BOT_TOKEN"]
    chat_id = os.environ["CHAT_ID"]
    gammu_config = os.environ.get("GAMMU_CONFIG", "/etc/gammurc")
    signal_threshold = int(os.environ.get("SIGNAL_WARN_THRESHOLD", "20"))
    interval = float(os.environ.get("MONITOR_INTERVAL_SECONDS", "60"))

    client = TelegramClient(bot_token=bot_token)

    connection_alert_active = False
    signal_alert_active = False
    error_alert_active = False

    print(
        f"event=monitor_startup"
        f" gammu_config={gammu_config}"
        f" signal_threshold={signal_threshold}"
        f" interval={interval}"
    )

    while True:
        status, error_msg = run_monitor(gammu_config)

        if error_msg is not None:
            print(f"event=monitor_error error={error_msg!r}")
            if not error_alert_active:
                _send_alert(client, chat_id, f"Modem: monitor tool failing — {error_msg}")
                error_alert_active = True
            time.sleep(interval)
            continue

        if error_alert_active:
            _send_alert(client, chat_id, "Modem: monitor tool recovered")
            error_alert_active = False

        assert status is not None

        connection_lost = (
            status.network_state in _LOST_STATES
            or status.network_state is None
        )
        if connection_lost and not connection_alert_active:
            state_label = status.network_state or "unknown"
            _send_alert(client, chat_id, f"Modem: network connection lost (state: {state_label})")
            connection_alert_active = True
        elif not connection_lost and connection_alert_active:
            _send_alert(client, chat_id, f"Modem: network connection restored (state: {status.network_state})")
            connection_alert_active = False

        signal_low = (
            status.signal_percent is not None
            and status.signal_percent < signal_threshold
        )
        if signal_low and not signal_alert_active:
            _send_alert(
                client,
                chat_id,
                f"Modem: signal low ({status.signal_percent}% — below threshold {signal_threshold}%)",
            )
            signal_alert_active = True
        elif not signal_low and signal_alert_active and status.signal_percent is not None:
            _send_alert(client, chat_id, f"Modem: signal recovered ({status.signal_percent}%)")
            signal_alert_active = False

        print(
            f"event=monitor_poll"
            f" signal_percent={status.signal_percent}"
            f" network_state={status.network_state!r}"
            f" connection_alert={connection_alert_active}"
            f" signal_alert={signal_alert_active}"
        )

        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())
