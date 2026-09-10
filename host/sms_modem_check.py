#!/usr/bin/env python3
"""Alert when the modem has data service but no SMS service.

Runs on the HOST from the repo checkout, driven by a systemd timer.
Must stay Python 3.12 compatible.

This check exists because gammu-smsd-monitor reports signal, IMEI and counters
but no registration state at all. A modem can look perfectly healthy while
being unable to receive a single SMS.
"""
from __future__ import annotations

import json
import os

STATE_PATH = "/var/lib/sms-modem-check/state.json"

_DEFAULT_STATE = {"consecutive_failures": 0, "alert_active": False}


def load_state(path: str = STATE_PATH) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return dict(_DEFAULT_STATE)
    return {
        "consecutive_failures": int(data.get("consecutive_failures", 0)),
        "alert_active": bool(data.get("alert_active", False)),
    }


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
