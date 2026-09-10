#!/usr/bin/env python3
"""Force the modem to re-register, restoring circuit-switched (SMS) service.

Runs on the HOST from the repo checkout. Must stay Python 3.12 compatible.

This is the only tool here that writes to the modem. It drops the modem off the
network for roughly 2.5 minutes, which is why it is never run automatically.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sms_modem_at  # noqa: E402


def reregister(
    *,
    port: str = sms_modem_at.DIAG_PORT,
    timeout: float = 180.0,
    poll_interval: float = 10.0,
    transport=None,
    sleep=time.sleep,
    now=time.monotonic,
    out=print,
) -> bool:
    """Deregister, re-register, and wait for CS service. True if restored."""
    out("Deregistering (AT+COPS=2)...")
    # This firmware answers ERROR here. That is expected, not a failure.
    sms_modem_at.query(["AT+COPS=2"], port=port, transport=transport)

    out("Re-registering (AT+COPS=0). This drops the modem off the network.")
    sms_modem_at.query(["AT+COPS=0"], port=port, transport=transport)

    deadline_start = now()
    while now() - deadline_start < timeout:
        raw = sms_modem_at.query(["AT^SYSINFO"], port=port, transport=transport)
        status = sms_modem_at.parse_sysinfo(raw)
        out(
            "  srv_domain=%s srv_status=%s sms_capable=%s"
            % (status.srv_domain, status.srv_status, status.sms_capable)
        )
        if status.sms_capable:
            out("SMS service restored.")
            return True
        sleep(poll_interval)

    out("Timed out waiting for circuit-switched service.")
    return False


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Force modem re-registration to restore SMS service."
    )
    parser.add_argument("--port", default=sms_modem_at.DIAG_PORT)
    parser.add_argument("--timeout", type=float, default=180.0)
    args = parser.parse_args(argv)

    print("Recovery normally takes 1-3 minutes and passes through")
    print("srv_domain=4 with no signal. That is expected; wait it out.")
    ok = reregister(port=args.port, timeout=args.timeout)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
