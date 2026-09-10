"""AT-command access to the modem's diagnostic port.

Runs on the HOST, not in the container, and must stay Python 3.12 compatible.

gammu-smsd owns the if00 port; everything here uses if01, which gammu does not
touch. Concurrent use of the two interfaces was verified safe by hand.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
import re
import termios
import time

# Never address the modem as /dev/ttyUSB2: that name changes on every USB
# re-enumeration, and this modem re-enumerates on its own.
DIAG_PORT = "/dev/serial/by-id/usb-HUAWEI_Technologies_HUAWEI_Mobile-if01-port0"

_SYSINFO_RE = re.compile(r"\^SYSINFO:\s*(\d+),(\d+),(\d+),(\d+),(\d+)")

# srv_domain values reported by ^SYSINFO.
_DOMAIN_CS_ONLY = 1
_DOMAIN_CS_PS = 3


@dataclass
class ServiceStatus:
    srv_status: int | None
    srv_domain: int | None
    roam_status: int | None
    sys_mode: int | None
    sim_state: int | None

    @property
    def sms_capable(self) -> bool:
        """SMS ride the circuit-switched domain.

        A modem can report full signal and a valid SIM while registered for
        packet service only, in which case no SMS can ever arrive.
        """
        return self.srv_domain in (_DOMAIN_CS_ONLY, _DOMAIN_CS_PS)


def parse_sysinfo(text: str) -> ServiceStatus:
    match = _SYSINFO_RE.search(text)
    if match is None:
        return ServiceStatus(None, None, None, None, None)
    values = [int(g) for g in match.groups()]
    return ServiceStatus(*values)


def open_port(path: str) -> int:
    """Open the diagnostic port in raw mode with HUPCL cleared.

    HUPCL matters: with it set, closing the port drops DTR, which can reset the
    modem. The checker opens this port every few minutes.
    """
    fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    try:
        attrs = termios.tcgetattr(fd)
        iflag, oflag, cflag, lflag, ispeed, ospeed, cc = attrs

        # Raw mode: no echo, no canonical processing, no signal characters.
        iflag = 0
        oflag = 0
        lflag = 0
        cflag |= termios.CREAD | termios.CLOCAL
        cflag &= ~termios.HUPCL

        # ispeed/ospeed are passed through untouched on purpose.
        termios.tcsetattr(fd, termios.TCSANOW, [iflag, oflag, cflag, lflag, ispeed, ospeed, cc])
    except Exception:
        os.close(fd)
        raise
    return fd


def query(
    commands: list[str],
    *,
    port: str = DIAG_PORT,
    settle: float = 1.2,
    transport=None,
) -> str:
    """Send AT commands and return the accumulated reply text.

    `transport` lets tests substitute the serial layer entirely.
    """
    if transport is not None:
        return transport(commands)

    fd = open_port(port)
    try:
        chunks = []
        for command in commands:
            os.write(fd, (command + "\r").encode("ascii"))
            time.sleep(settle)
            chunks.append(_drain(fd))
        return "".join(chunks)
    finally:
        os.close(fd)


def _drain(fd: int) -> str:
    """Read all currently-available bytes from `fd` without blocking.

    `BlockingIOError` (EAGAIN/EWOULDBLOCK) just means the read buffer is
    empty right now, which is the normal way this loop ends. Any other
    `OSError` (e.g. ENODEV, EIO) means the modem itself is gone or faulted
    and must propagate rather than be swallowed as silently truncated
    output.
    """
    out = []
    while True:
        try:
            data = os.read(fd, 4096)
        except BlockingIOError:
            break
        if not data:
            break
        out.append(data.decode("ascii", errors="replace"))
    return "".join(out)
