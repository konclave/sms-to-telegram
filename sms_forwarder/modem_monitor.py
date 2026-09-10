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

# gammu-smsd-monitor reports daemon state as bare "Key: value" lines. It does
# not expose a network registration state at all -- the "Signal strength" and
# "Network" lines this once looked for come from `gammu --monitor`, which
# cannot be used here because gammu-smsd holds the serial port.
_SIGNAL_RE = re.compile(r"^NetworkSignal:[ \t]*(\d+)[ \t]*$", re.MULTILINE)
_IMEI_RE = re.compile(r"^IMEI:[ \t]*(\S+)[ \t]*$", re.MULTILINE)


@dataclass
class ModemStatus:
    signal_percent: int | None
    imei: str | None

    @property
    def reachable(self) -> bool:
        """gammu-smsd leaves the identity fields empty until it reaches the phone."""
        return self.imei is not None


def parse_monitor_output(text: str) -> ModemStatus:
    signal_match = _SIGNAL_RE.search(text)
    imei_match = _IMEI_RE.search(text)
    return ModemStatus(
        signal_percent=int(signal_match.group(1)) if signal_match else None,
        imei=imei_match.group(1) if imei_match else None,
    )


def run_monitor(gammu_config: str) -> tuple[ModemStatus | None, str | None]:
    try:
        result = subprocess.run(
            ["gammu-smsd-monitor", "-c", gammu_config, "-n", "1", "-d", "1"],
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


class AlertState:
    """Alert edge-detection for the monitor loop.

    Kept separate from main() so tests exercise the real decision logic rather
    than a reimplementation of it.
    """

    def __init__(self, signal_threshold: int = 20):
        self.signal_threshold = signal_threshold
        self.connection_alert_active = False
        self.signal_alert_active = False
        self.error_alert_active = False

    def evaluate(self, status: ModemStatus | None, error_msg: str | None) -> list[str]:
        """Return the alert texts this poll should send."""
        alerts: list[str] = []

        if error_msg is not None:
            if not self.error_alert_active:
                alerts.append(f"Modem: monitor tool failing — {error_msg}")
                self.error_alert_active = True
            return alerts

        if self.error_alert_active:
            alerts.append("Modem: monitor tool recovered")
            self.error_alert_active = False

        assert status is not None

        connection_lost = not status.reachable
        if connection_lost and not self.connection_alert_active:
            alerts.append("Modem: unreachable (gammu-smsd reports no IMEI)")
            self.connection_alert_active = True
        elif not connection_lost and self.connection_alert_active:
            alerts.append(f"Modem: reachable again (IMEI {status.imei})")
            self.connection_alert_active = False

        if connection_lost:
            # Signal figures are meaningless with no modem; hold the signal
            # alert state rather than firing a second alert for one fault.
            return alerts

        signal_low = (
            status.signal_percent is not None
            and status.signal_percent < self.signal_threshold
        )
        if signal_low and not self.signal_alert_active:
            alerts.append(
                f"Modem: signal low ({status.signal_percent}% —"
                f" below threshold {self.signal_threshold}%)"
            )
            self.signal_alert_active = True
        elif not signal_low and self.signal_alert_active and status.signal_percent is not None:
            alerts.append(f"Modem: signal recovered ({status.signal_percent}%)")
            self.signal_alert_active = False

        return alerts


def main() -> None:
    bot_token = os.environ["BOT_TOKEN"]
    chat_id = os.environ["CHAT_ID"]
    gammu_config = os.environ.get("GAMMU_CONFIG", "/etc/gammurc")
    signal_threshold = int(os.environ.get("SIGNAL_WARN_THRESHOLD", "20"))
    interval = float(os.environ.get("MONITOR_INTERVAL_SECONDS", "60"))

    client = TelegramClient(bot_token=bot_token)

    state = AlertState(signal_threshold=signal_threshold)

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

        for text in state.evaluate(status, error_msg):
            _send_alert(client, chat_id, text)

        if status is not None:
            print(
                f"event=monitor_poll"
                f" signal_percent={status.signal_percent}"
                f" imei={status.imei!r}"
                f" connection_alert={state.connection_alert_active}"
                f" signal_alert={state.signal_alert_active}"
            )

        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())
