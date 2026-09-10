#!/usr/bin/env python3
"""Alert when the modem has data service but no SMS service.

Runs on the HOST from the repo checkout, driven by a systemd timer.
Must stay Python 3.12 compatible.

This check exists because gammu-smsd-monitor reports signal, IMEI and counters
but no registration state at all. A modem can look perfectly healthy while
being unable to receive a single SMS.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sms_modem_at  # noqa: E402

STATE_PATH = "/var/lib/sms-modem-check/state.json"
CREDENTIALS_PATH = "/etc/systemd-notify.env"

_DEFAULT_STATE = {"consecutive_failures": 0, "alert_active": False}


def load_state(path: str = STATE_PATH) -> dict:
    """Never let a bad state file stop the check from running.

    This runs unattended every few minutes, so every failure mode -- missing
    file, invalid JSON, valid JSON of the wrong shape -- degrades to defaults.
    """
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            return dict(_DEFAULT_STATE)
        failures = data.get("consecutive_failures", 0)
        active = data.get("alert_active", False)
        if not isinstance(failures, int) or isinstance(failures, bool):
            failures = 0
        if not isinstance(active, bool):
            active = False
        return {"consecutive_failures": failures, "alert_active": active}
    except (OSError, ValueError, TypeError):
        return dict(_DEFAULT_STATE)


def save_state(path: str, state: dict) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(state, handle)


_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The alert is read on a phone, so it carries the real command, not a
# placeholder the reader has to resolve.
_ALERT_LOST = (
    "Modem: no SMS service — registered on the packet-switched domain only "
    "(srv_domain=2). Data works; SMS cannot arrive.\n"
    "Fix: cd %s && sudo ./host/sms_modem_reregister.py" % _REPO_ROOT
)
_ALERT_RESTORED = "Modem: SMS service restored (circuit-switched domain registered)."


def evaluate(state: dict, sms_capable: bool, *, alert_after: int = 2) -> list[str]:
    """Update `state` for one check and return the alerts to send.

    Requiring consecutive failures avoids alerting on a modem that is merely
    mid-scan; recovery passes through a non-capable state for a minute or more.
    """
    alerts: list[str] = []

    if not sms_capable:
        state["consecutive_failures"] += 1
        if state["consecutive_failures"] >= alert_after and not state["alert_active"]:
            alerts.append(_ALERT_LOST)
            state["alert_active"] = True
        return alerts

    state["consecutive_failures"] = 0
    if state["alert_active"]:
        alerts.append(_ALERT_RESTORED)
        state["alert_active"] = False
    return alerts


def read_credentials(path: str = CREDENTIALS_PATH) -> tuple:
    token = None
    chat = None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            lines = handle.readlines()
    except OSError:
        return (None, None)

    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip('"').strip("'")
        if key.strip() == "BOT_TOKEN":
            token = value
        elif key.strip() == "CHAT_ID":
            chat = value
    return (token, chat)


def send_alert(bot_token: str, chat_id: str, text: str, *, sender=None) -> None:
    url = "https://api.telegram.org/bot%s/sendMessage" % bot_token
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode("utf-8")
    if sender is not None:
        sender(url, data)
        return
    request = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(request, timeout=15):
        pass


def main(argv=None, *, transport=None, sender=None) -> int:
    parser = argparse.ArgumentParser(description="Alert when the modem loses SMS service.")
    parser.add_argument("--port", default=sms_modem_at.DIAG_PORT)
    parser.add_argument("--state", default=STATE_PATH)
    parser.add_argument("--credentials", default=CREDENTIALS_PATH)
    parser.add_argument("--alert-after", type=int, default=2)
    args = parser.parse_args(argv)

    if not os.path.exists(args.port):
        print("modem not present at %s; nothing to check" % args.port)
        return 0

    try:
        raw = sms_modem_at.query(["AT^SYSINFO"], port=args.port, transport=transport)
    except OSError as exc:
        # A checker fault is not a modem fault: say so, alert nobody.
        print("query failed: %s" % exc)
        return 0

    status = sms_modem_at.parse_sysinfo(raw)
    if status.srv_domain is None:
        print("query failed: no ^SYSINFO in reply")
        return 0

    state = load_state(args.state)
    alerts = evaluate(state, status.sms_capable, alert_after=args.alert_after)
    save_state(args.state, state)

    print(
        "srv_domain=%s sms_capable=%s failures=%s alert_active=%s"
        % (status.srv_domain, status.sms_capable,
           state["consecutive_failures"], state["alert_active"])
    )

    if alerts:
        token, chat = read_credentials(args.credentials)
        if not token or not chat:
            print("alert suppressed: credentials unavailable")
            return 0
        for text in alerts:
            send_alert(token, chat, text, sender=sender)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
