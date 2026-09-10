# Modem CS-Registration Monitoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect when the modem holds data service but has lost circuit-switched service (so SMS silently cannot arrive), alert to Telegram, and provide a manual handle that forces re-registration.

**Architecture:** Three stdlib-only Python modules in `host/`, run from the repo checkout on the host — never inside the container, because `podman exec` needs a live container and the handle must work when the container is down. A systemd timer runs the checker every 5 minutes; the handle is run by hand. `setup.sh` installs only two unit files; no Python is copied anywhere.

**Tech Stack:** Python stdlib only (`termios`, `urllib`, `json`, `re`, `dataclasses`). pytest for tests. systemd timer + oneshot. No new dependencies — the project declares `dependencies = []` and must keep doing so.

**Spec:** `docs/superpowers/specs/2026-09-10-modem-cs-registration-monitoring-design.md`

## Global Constraints

- **No new dependencies.** `pyproject.toml` has `dependencies = []`. Do not add pyserial or anything else.
- **`host/` modules must run on Python 3.12.** The host has 3.12.7; the project targets `>=3.14,<3.15`. Do not use syntax or stdlib APIs newer than 3.12 in `host/`.
  Because `requires-python` excludes 3.12, the 3.12 test run must bypass the project:
  `uv run --python 3.12 --no-project --with pytest==8.4.1 pytest <file>`. A plain
  `uv run --python 3.12 pytest` fails on the version constraint.
- **Address the modem by its by-id path**, never `/dev/ttyUSB2`. The correct path is `/dev/serial/by-id/usb-HUAWEI_Technologies_HUAWEI_Mobile-if01-port0`. Device names change on every re-enumeration.
- **`HUPCL` must be cleared** on the serial port before use. Otherwise closing the port drops DTR and can reset the modem.
- **Never set the baud rate.** The device rejects it and it is meaningless on USB serial.
- **The checker never writes to the modem.** Only `sms_modem_reregister.py` issues write commands.
- `if00` belongs to gammu. These tools use `if01` only.
- Interface number in the by-id path is `if01`; do not confuse with `if00`.

---

### Task 1: AT client and `^SYSINFO` parser

**Files:**
- Create: `host/sms_modem_at.py`
- Create: `tests/conftest.py`
- Test: `tests/test_host_modem_at.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `ServiceStatus` dataclass with fields `srv_status: int | None`, `srv_domain: int | None`, `roam_status: int | None`, `sys_mode: int | None`, `sim_state: int | None`, and property `sms_capable: bool`.
  - `parse_sysinfo(text: str) -> ServiceStatus`
  - `DIAG_PORT: str` module constant (the by-id path)
  - `open_port(path: str) -> int` returning a file descriptor with raw mode and `HUPCL` cleared
  - `query(commands: list[str], *, port: str = DIAG_PORT, settle: float = 1.2, transport=None) -> str`

`transport` is an optional callable `(commands: list[str]) -> str` used by tests to bypass hardware. When `None`, real serial I/O is used.

- [ ] **Step 1: Create the conftest so `host/` is importable**

The repo has no `tests/conftest.py`. `pytest.ini` sets `pythonpath = .`, which puts the repo root on the path but not `host/`.

Create `tests/conftest.py`:

```python
import sys
from pathlib import Path

# host/ modules are executed directly on the host (sys.path[0] is host/), so
# they import each other by plain name. Mirror that for tests.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "host"))
```

- [ ] **Step 2: Write the failing parser tests**

Create `tests/test_host_modem_at.py`. The two `^SYSINFO` strings are real captures from the 2026-09-10 incident.

```python
from sms_modem_at import ServiceStatus, parse_sysinfo

# Captured while SMS were silently not arriving.
_PS_ONLY = """AT^SYSINFO
^SYSINFO:2,2,1,3,1,0,3
OK
"""

# Captured after AT+COPS=0 restored circuit-switched service.
_HEALTHY = """AT^SYSINFO
^SYSINFO:2,3,1,3,1,0,3
OK
"""


def test_parses_all_sysinfo_fields():
    s = parse_sysinfo(_HEALTHY)
    assert s.srv_status == 2
    assert s.srv_domain == 3
    assert s.roam_status == 1
    assert s.sys_mode == 3
    assert s.sim_state == 1


def test_ps_only_is_not_sms_capable():
    """srv_domain 2 = packet-switched only. Data works, SMS cannot arrive."""
    assert parse_sysinfo(_PS_ONLY).sms_capable is False


def test_cs_and_ps_is_sms_capable():
    assert parse_sysinfo(_HEALTHY).sms_capable is True


def test_cs_only_is_sms_capable():
    """srv_domain 1 = CS only. Unusual, but SMS work."""
    assert parse_sysinfo("^SYSINFO:2,1,1,3,1,0,3\n").sms_capable is True


def test_searching_is_not_sms_capable():
    """srv_domain 4 = registering. Transient during recovery."""
    assert parse_sysinfo("^SYSINFO:1,4,0,0,1,0,0\n").sms_capable is False


def test_no_service_is_not_sms_capable():
    assert parse_sysinfo("^SYSINFO:0,0,0,0,1,0,0\n").sms_capable is False


def test_unparseable_output_yields_empty_status_not_an_exception():
    """A garbled reply must never crash the checker."""
    s = parse_sysinfo("GARBAGE\r\nOK\r\n")
    assert s == ServiceStatus(None, None, None, None, None)
    assert s.sms_capable is False


def test_empty_output_is_not_sms_capable():
    assert parse_sysinfo("").sms_capable is False
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_host_modem_at.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sms_modem_at'`

- [ ] **Step 4: Write the parser**

Create `host/sms_modem_at.py`:

```python
#!/usr/bin/env python3
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
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_host_modem_at.py -v`
Expected: PASS (8 tests)

- [ ] **Step 6: Write the failing port-configuration test**

`HUPCL` is the single most dangerous detail here: if it is left set, closing the
port drops DTR and can reset the modem — and the checker opens the port every
five minutes.

Append to `tests/test_host_modem_at.py`, moving the three imports to the top of the file with the existing ones:

```python
import termios
from unittest.mock import patch

import sms_modem_at


def test_open_port_clears_hupcl_so_closing_does_not_reset_the_modem():
    captured = {}

    def fake_tcsetattr(fd, when, attrs):
        captured["attrs"] = attrs

    # A plausible default termios list: HUPCL set in the control flags.
    default = [0, 0, termios.HUPCL | termios.CREAD | termios.CLOCAL, 0, 0, 0, [0] * 32]

    with patch("sms_modem_at.os.open", return_value=7), \
         patch("sms_modem_at.termios.tcgetattr", return_value=default), \
         patch("sms_modem_at.termios.tcsetattr", side_effect=fake_tcsetattr):
        fd = sms_modem_at.open_port("/dev/null")

    assert fd == 7
    cflag = captured["attrs"][2]
    assert not cflag & termios.HUPCL, "HUPCL still set: closing the port will reset the modem"


def test_open_port_does_not_set_a_baud_rate():
    """The device rejects stty 115200 and baud is meaningless on USB serial."""
    captured = {}

    def fake_tcsetattr(fd, when, attrs):
        captured["attrs"] = attrs

    default = [0, 0, termios.HUPCL, 0, termios.B9600, termios.B9600, [0] * 32]

    with patch("sms_modem_at.os.open", return_value=7), \
         patch("sms_modem_at.termios.tcgetattr", return_value=default), \
         patch("sms_modem_at.termios.tcsetattr", side_effect=fake_tcsetattr):
        sms_modem_at.open_port("/dev/null")

    assert captured["attrs"][4] == termios.B9600
    assert captured["attrs"][5] == termios.B9600
```

- [ ] **Step 7: Run to verify it fails**

Run: `uv run pytest tests/test_host_modem_at.py -k open_port -v`
Expected: FAIL — `AttributeError: module 'sms_modem_at' has no attribute 'open_port'`

- [ ] **Step 8: Implement `open_port` and `query`**

Append to `host/sms_modem_at.py`:

```python
def open_port(path: str) -> int:
    """Open the diagnostic port in raw mode with HUPCL cleared.

    HUPCL matters: with it set, closing the port drops DTR, which can reset the
    modem. The checker opens this port every few minutes.
    """
    fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
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
    out = []
    while True:
        try:
            data = os.read(fd, 4096)
        except BlockingIOError:
            break
        except OSError:
            break
        if not data:
            break
        out.append(data.decode("ascii", errors="replace"))
    return "".join(out)
```

- [ ] **Step 9: Run the full file to verify it passes**

Run: `uv run pytest tests/test_host_modem_at.py -v`
Expected: PASS (10 tests)

- [ ] **Step 10: Verify it runs on Python 3.12**

The host runs 3.12.7 while the project targets 3.14. A 3.14-only construct
would pass above and fail only in production.

Run: `uv run --python 3.12 --no-project --with pytest==8.4.1 pytest tests/test_host_modem_at.py -v`
Expected: PASS (10 tests)

- [ ] **Step 11: Commit**

```bash
git add host/sms_modem_at.py tests/conftest.py tests/test_host_modem_at.py
git commit -m "feat: add AT client and SYSINFO parser for modem diagnostics"
```

---

### Task 2: Alert state with persistence

**Files:**
- Create: `host/sms_modem_check.py`
- Test: `tests/test_host_modem_check.py`

**Interfaces:**
- Consumes: `sms_modem_at.ServiceStatus`, `sms_modem_at.parse_sysinfo`, `sms_modem_at.query` from Task 1.
- Produces:
  - `STATE_PATH: str` = `/var/lib/sms-modem-check/state.json`
  - `load_state(path) -> dict` with keys `consecutive_failures: int`, `alert_active: bool`
  - `save_state(path, state: dict) -> None`
  - `evaluate(state: dict, sms_capable: bool, *, alert_after: int = 2) -> list[str]` — mutates `state`, returns alert texts to send

The checker is a timer-driven oneshot with no memory between runs, so the
debounce counter has to live on disk. Semantics mirror `AlertState` in
`sms_forwarder/modem_monitor.py`: alert once on sustained failure, once on
recovery, silent otherwise.

- [ ] **Step 1: Write the failing state tests**

Create `tests/test_host_modem_check.py`:

```python
import json

import sms_modem_check


def test_missing_state_file_yields_defaults(tmp_path):
    """A missing state file must never stop the check from running."""
    state = sms_modem_check.load_state(str(tmp_path / "nope.json"))
    assert state == {"consecutive_failures": 0, "alert_active": False}


def test_corrupt_state_file_yields_defaults(tmp_path):
    p = tmp_path / "state.json"
    p.write_text("{not json")
    assert sms_modem_check.load_state(str(p)) == {
        "consecutive_failures": 0,
        "alert_active": False,
    }


def test_state_round_trips(tmp_path):
    p = str(tmp_path / "state.json")
    sms_modem_check.save_state(p, {"consecutive_failures": 2, "alert_active": True})
    assert sms_modem_check.load_state(p) == {
        "consecutive_failures": 2,
        "alert_active": True,
    }


def test_save_state_creates_parent_directory(tmp_path):
    p = str(tmp_path / "sub" / "dir" / "state.json")
    sms_modem_check.save_state(p, {"consecutive_failures": 1, "alert_active": False})
    assert json.loads(open(p).read())["consecutive_failures"] == 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_host_modem_check.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sms_modem_check'`

- [ ] **Step 3: Implement state load/save**

Create `host/sms_modem_check.py`:

```python
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
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_host_modem_check.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Write the failing debounce tests**

Append to `tests/test_host_modem_check.py`:

```python
def _run(capable_sequence, alert_after=2):
    """Drive evaluate() across a sequence of checks, as the timer would."""
    state = {"consecutive_failures": 0, "alert_active": False}
    alerts = []
    for capable in capable_sequence:
        alerts.extend(sms_modem_check.evaluate(state, capable, alert_after=alert_after))
    return alerts


def test_healthy_modem_is_silent():
    assert _run([True, True, True]) == []


def test_single_failure_does_not_alert():
    """One bad reading may just be a modem mid-scan."""
    assert _run([False, True]) == []


def test_two_consecutive_failures_alert_once():
    alerts = _run([False, False, False, False])
    assert len(alerts) == 1
    assert "packet-switched" in alerts[0]


def test_recovery_sends_one_alert():
    alerts = _run([False, False, True])
    assert len(alerts) == 2
    assert "packet-switched" in alerts[0]
    assert "restored" in alerts[1]


def test_no_recovery_alert_without_a_prior_alert():
    assert _run([False, True, True]) == []


def test_failure_streak_resets_on_a_good_reading():
    assert _run([False, True, False, True]) == []


def test_alert_text_names_the_remedy_with_a_runnable_path():
    """The alert is read on a phone; it must carry a real command."""
    alerts = _run([False, False])
    assert "sms_modem_reregister.py" in alerts[0]
    assert "<repo>" not in alerts[0]
    assert "cd /" in alerts[0]
```

- [ ] **Step 6: Run to verify it fails**

Run: `uv run pytest tests/test_host_modem_check.py -v`
Expected: the four state tests PASS; the seven new debounce tests FAIL with `AttributeError: module 'sms_modem_check' has no attribute 'evaluate'`

- [ ] **Step 7: Implement `evaluate`**

Append to `host/sms_modem_check.py`:

```python
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
```

- [ ] **Step 8: Run to verify it passes**

Run: `uv run pytest tests/test_host_modem_check.py -v`
Expected: PASS (11 tests)

- [ ] **Step 9: Verify on Python 3.12**

Run: `uv run --python 3.12 --no-project --with pytest==8.4.1 pytest tests/test_host_modem_check.py -v`
Expected: PASS (11 tests)

- [ ] **Step 10: Commit**

```bash
git add host/sms_modem_check.py tests/test_host_modem_check.py
git commit -m "feat: add persistent debounce state for modem SMS-service checks"
```

---

### Task 3: Telegram alerting and the checker entry point

**Files:**
- Modify: `host/sms_modem_check.py`
- Test: `tests/test_host_modem_check.py`

**Interfaces:**
- Consumes: `evaluate`, `load_state`, `save_state` from Task 2; `query`, `parse_sysinfo`, `DIAG_PORT` from Task 1.
- Produces:
  - `read_credentials(path: str = "/etc/systemd-notify.env") -> tuple[str | None, str | None]` returning `(bot_token, chat_id)`
  - `send_alert(bot_token, chat_id, text, *, sender=None) -> None`
  - `main(argv=None) -> int`

`sender` is an optional callable `(url: str, data: bytes) -> None` for tests.

- [ ] **Step 1: Write the failing credentials test**

The env file is the same one `on-failure.sh` already uses, so no credentials are
duplicated. It is shell-style `KEY=value`, one per line.

Append to `tests/test_host_modem_check.py`:

```python
def test_reads_credentials_from_env_file(tmp_path):
    p = tmp_path / "notify.env"
    p.write_text('BOT_TOKEN=123:abc\nCHAT_ID=-100999\n')
    token, chat = sms_modem_check.read_credentials(str(p))
    assert token == "123:abc"
    assert chat == "-100999"


def test_credentials_tolerate_quotes_and_blank_lines(tmp_path):
    p = tmp_path / "notify.env"
    p.write_text('\n# comment\nBOT_TOKEN="123:abc"\n\nCHAT_ID=\'-100999\'\n')
    assert sms_modem_check.read_credentials(str(p)) == ("123:abc", "-100999")


def test_missing_credentials_file_returns_none(tmp_path):
    assert sms_modem_check.read_credentials(str(tmp_path / "nope")) == (None, None)
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_host_modem_check.py -k credentials -v`
Expected: FAIL — `AttributeError: module 'sms_modem_check' has no attribute 'read_credentials'`

- [ ] **Step 3: Implement credentials and sending**

Append to `host/sms_modem_check.py`:

```python
import urllib.parse
import urllib.request

CREDENTIALS_PATH = "/etc/systemd-notify.env"


def read_credentials(path: str = CREDENTIALS_PATH) -> tuple:  # (str|None, str|None)
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
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_host_modem_check.py -k credentials -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Write the failing `main()` tests**

Append to `tests/test_host_modem_check.py`:

```python
import sms_modem_at


def test_main_exits_quietly_when_the_modem_is_absent(tmp_path, capsys):
    """Modem absence is covered by the udev rule; this check is only about a
    modem that is present but not CS-registered."""
    rc = sms_modem_check.main([
        "--port", str(tmp_path / "no-such-port"),
        "--state", str(tmp_path / "state.json"),
        "--credentials", str(tmp_path / "creds"),
    ])
    assert rc == 0
    assert "not present" in capsys.readouterr().out


def test_main_alerts_after_two_ps_only_readings(tmp_path):
    port = tmp_path / "port"
    port.write_text("")
    sent = []
    state = str(tmp_path / "state.json")
    creds = tmp_path / "creds"
    creds.write_text("BOT_TOKEN=t\nCHAT_ID=c\n")

    def fake_transport(commands):
        return "^SYSINFO:2,2,1,3,1,0,3\nOK\n"

    for _ in range(2):
        rc = sms_modem_check.main(
            ["--port", str(port), "--state", state, "--credentials", str(creds)],
            transport=fake_transport,
            sender=lambda url, data: sent.append((url, data)),
        )
        assert rc == 0

    assert len(sent) == 1
    assert b"packet-switched" in sent[0][1]


def test_main_is_silent_while_sms_service_is_healthy(tmp_path):
    port = tmp_path / "port"
    port.write_text("")
    sent = []
    creds = tmp_path / "creds"
    creds.write_text("BOT_TOKEN=t\nCHAT_ID=c\n")

    for _ in range(3):
        sms_modem_check.main(
            ["--port", str(port), "--state", str(tmp_path / "s.json"),
             "--credentials", str(creds)],
            transport=lambda commands: "^SYSINFO:2,3,1,3,1,0,3\nOK\n",
            sender=lambda url, data: sent.append((url, data)),
        )
    assert sent == []


def test_main_does_not_alert_when_the_query_fails(tmp_path, capsys):
    """A checker fault is not a modem fault. Never alert about ourselves."""
    port = tmp_path / "port"
    port.write_text("")
    sent = []
    creds = tmp_path / "creds"
    creds.write_text("BOT_TOKEN=t\nCHAT_ID=c\n")

    def boom(commands):
        raise OSError("port busy")

    for _ in range(3):
        rc = sms_modem_check.main(
            ["--port", str(port), "--state", str(tmp_path / "s.json"),
             "--credentials", str(creds)],
            transport=boom,
            sender=lambda url, data: sent.append((url, data)),
        )
        assert rc == 0
    assert sent == []
    assert "query failed" in capsys.readouterr().out
```

- [ ] **Step 6: Run to verify it fails**

Run: `uv run pytest tests/test_host_modem_check.py -k main -v`
Expected: FAIL — `AttributeError: module 'sms_modem_check' has no attribute 'main'`

- [ ] **Step 7: Implement `main`**

Append to `host/sms_modem_check.py`:

```python
import argparse
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sms_modem_at  # noqa: E402


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
    was_alerting = state["alert_active"]
    alerts = evaluate(state, status.sms_capable, alert_after=args.alert_after)

    print(
        "srv_domain=%s sms_capable=%s failures=%s alert_active=%s"
        % (status.srv_domain, status.sms_capable,
           state["consecutive_failures"], state["alert_active"])
    )

    if alerts:
        token, chat = read_credentials(args.credentials)
        if not token or not chat:
            # Roll the latch back before persisting: recording an alert we
            # never sent would suppress it forever, leaving a real outage
            # silent. The failure counter still advances.
            state["alert_active"] = was_alerting
            save_state(args.state, state)
            print("alert suppressed: credentials unavailable")
            return 0
        for text in alerts:
            send_alert(token, chat, text, sender=sender)

    save_state(args.state, state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 8: Run the whole file to verify it passes**

Run: `uv run pytest tests/test_host_modem_check.py -v`
Expected: PASS (18 tests)

- [ ] **Step 9: Verify on Python 3.12**

Run: `uv run --python 3.12 --no-project --with pytest==8.4.1 pytest tests/test_host_modem_check.py -v`
Expected: PASS (18 tests)

- [ ] **Step 10: Make it executable and commit**

```bash
chmod +x host/sms_modem_check.py
git add host/sms_modem_check.py tests/test_host_modem_check.py
git commit -m "feat: alert to Telegram when the modem loses SMS service"
```

---

### Task 4: The re-registration handle

**Files:**
- Create: `host/sms_modem_reregister.py`
- Test: `tests/test_host_modem_reregister.py`

**Interfaces:**
- Consumes: `query`, `parse_sysinfo`, `DIAG_PORT` from Task 1.
- Produces: `reregister(*, port, timeout=180.0, poll_interval=10.0, transport=None, sleep=time.sleep, now=time.monotonic, out=print) -> bool` and `main(argv=None) -> int`.

This is the only component that writes to the modem.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_host_modem_reregister.py`:

```python
import sms_modem_reregister


class FakeModem:
    """Answers AT commands; flips to CS+PS after `flip_after` SYSINFO polls."""

    def __init__(self, flip_after=2, cops2_errors=True):
        self.flip_after = flip_after
        self.cops2_errors = cops2_errors
        self.sysinfo_polls = 0
        self.commands = []

    def __call__(self, commands):
        self.commands.extend(commands)
        command = commands[0]
        if command == "AT+COPS=2":
            return "AT+COPS=2\nERROR\n" if self.cops2_errors else "AT+COPS=2\nOK\n"
        if command == "AT+COPS=0":
            return "AT+COPS=0\nOK\n"
        if command == "AT^SYSINFO":
            self.sysinfo_polls += 1
            if self.sysinfo_polls > self.flip_after:
                return "^SYSINFO:2,3,1,3,1,0,3\nOK\n"
            return "^SYSINFO:1,4,0,0,1,0,0\nOK\n"
        return "OK\n"


def test_reregister_succeeds_once_cs_service_returns():
    modem = FakeModem(flip_after=2)
    ok = sms_modem_reregister.reregister(
        port="/dev/null", transport=modem, sleep=lambda s: None, out=lambda *a: None
    )
    assert ok is True
    assert "AT+COPS=0" in modem.commands


def test_cops2_error_is_tolerated():
    """This firmware answers ERROR to AT+COPS=2; COPS=0 still works."""
    modem = FakeModem(flip_after=1, cops2_errors=True)
    ok = sms_modem_reregister.reregister(
        port="/dev/null", transport=modem, sleep=lambda s: None, out=lambda *a: None
    )
    assert ok is True


def test_reregister_times_out_when_cs_never_returns():
    """If the network genuinely refuses CS service, report honestly."""
    modem = FakeModem(flip_after=10_000)
    ticks = iter([0.0] + [i * 30.0 for i in range(1, 20)])
    ok = sms_modem_reregister.reregister(
        port="/dev/null",
        transport=modem,
        sleep=lambda s: None,
        now=lambda: next(ticks),
        timeout=60.0,
        out=lambda *a: None,
    )
    assert ok is False


def test_progress_is_printed_during_recovery():
    """Recovery passes through srv_domain 4 with no signal for a minute or
    more. Silence there looks like a hang and gets abandoned."""
    lines = []
    modem = FakeModem(flip_after=2)
    sms_modem_reregister.reregister(
        port="/dev/null", transport=modem, sleep=lambda s: None, out=lines.append
    )
    printed = " ".join(str(line) for line in lines)
    assert "srv_domain" in printed


def test_main_returns_zero_on_success(monkeypatch):
    monkeypatch.setattr(sms_modem_reregister, "reregister", lambda **kw: True)
    assert sms_modem_reregister.main(["--port", "/dev/null"]) == 0


def test_main_returns_one_on_timeout(monkeypatch):
    monkeypatch.setattr(sms_modem_reregister, "reregister", lambda **kw: False)
    assert sms_modem_reregister.main(["--port", "/dev/null"]) == 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_host_modem_reregister.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sms_modem_reregister'`

- [ ] **Step 3: Implement the handle**

Create `host/sms_modem_reregister.py`:

```python
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
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_host_modem_reregister.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Verify on Python 3.12**

Run: `uv run --python 3.12 --no-project --with pytest==8.4.1 pytest tests/test_host_modem_reregister.py -v`
Expected: PASS (6 tests)

- [ ] **Step 6: Make it executable and commit**

```bash
chmod +x host/sms_modem_reregister.py
git add host/sms_modem_reregister.py tests/test_host_modem_reregister.py
git commit -m "feat: add manual modem re-registration handle"
```

---

### Task 5: systemd timer and setup.sh installation

**Files:**
- Create: `sms-modem-check.service`
- Create: `sms-modem-check.timer`
- Modify: `setup.sh` (add `install_modem_check`, call it in `main`)
- Test: `tests/test_setup_script.py`

**Interfaces:**
- Consumes: `host/sms_modem_check.py` from Task 3.
- Produces: installed units at `$SYSTEMD_UNIT_DIR/sms-modem-check.{service,timer}`.

The units are installed because systemd requires them in `/etc/systemd/system`,
but their `ExecStart` points back at the repo checkout, so `git pull` updates
the logic with nothing to reinstall.

- [ ] **Step 1: Write the failing installation test**

Append to `tests/test_setup_script.py`. Follow the existing fake-bin pattern in
that file exactly — `write_fake_bin`, `install_stub_body()`, `udevadm_stub_body()`
already exist there.

```python
def test_setup_installs_modem_check_timer(tmp_path):
    """The checker runs from the repo; only the units are installed, with
    ExecStart pointing back at the checkout so git pull is the update path."""
    repo_root = Path.cwd()
    repo = prepare_repo_copy(tmp_path, repo_root)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    write_fake_bin(fake_bin, "udevadm", udevadm_stub_body())
    log = tmp_path / "calls.log"

    write_fake_bin(
        fake_bin,
        "podman",
        "#!/bin/sh\n"
        "if [ \"$1\" = image ] && [ \"$2\" = inspect ]; then echo 'sha256:x'; exit 0; fi\n"
        "exit 0\n",
    )
    write_fake_bin(fake_bin, "sudo", "#!/bin/sh\nshift\nexec \"$@\"\n")
    write_fake_bin(fake_bin, "systemctl", "#!/bin/sh\necho \"systemctl:$@\" >> \"$CALLS_LOG\"\nexit 0\n")
    write_fake_bin(fake_bin, "install", install_stub_body())

    env = os.environ | {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "CALLS_LOG": str(log),
        "QUADLET_DIR": str(tmp_path / "quadlet"),
        "QUEUE_HOST_DIR": str(tmp_path / "queue"),
        "UDEV_RULE_DIR": str(tmp_path / "udev"),
        "SYSTEMD_UNIT_DIR": str(tmp_path / "units"),
        "STATE_DIR": str(repo / ".deploy"),
        "IMAGE_NAME": "ghcr.io/konclave/sms-to-telegram:latest",
    }

    result = subprocess.run(
        ["bash", str(repo / "setup.sh")], cwd=tmp_path, env=env,
        capture_output=True, text=True,
    )

    assert result.returncode == 0, result.stderr
    service = tmp_path / "units" / "sms-modem-check.service"
    timer = tmp_path / "units" / "sms-modem-check.timer"
    assert service.exists()
    assert timer.exists()

    # ExecStart must point at the checkout, not a copied file.
    assert f"{repo}/host/sms_modem_check.py" in service.read_text()

    calls = log.read_text()
    assert "systemctl:enable --now sms-modem-check.timer" in calls
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_setup_script.py::test_setup_installs_modem_check_timer -v`
Expected: FAIL — `assert False` on `service.exists()`

- [ ] **Step 3: Create the unit templates**

Create `sms-modem-check.service`. `__REPO_ROOT__` is substituted at install time.

```ini
[Unit]
Description=Check that the modem still has SMS (circuit-switched) service
Documentation=https://github.com/konclave/sms-to-telegram

[Service]
Type=oneshot
# Runs from the repo checkout so that git pull is the update path.
ExecStart=/usr/bin/python3 __REPO_ROOT__/host/sms_modem_check.py
```

Create `sms-modem-check.timer`:

```ini
[Unit]
Description=Periodic modem SMS-service check

[Timer]
# The condition is rare and slow-moving; each check costs about a second.
OnBootSec=5min
OnUnitActiveSec=5min
Unit=sms-modem-check.service

[Install]
WantedBy=timers.target
```

- [ ] **Step 4: Add the install function to setup.sh**

In `setup.sh`, next to the existing `UDEV_RULE_SOURCE` block, add:

```bash
CHECK_SERVICE_SOURCE="${CHECK_SERVICE_SOURCE:-$REPO_ROOT/sms-modem-check.service}"
CHECK_TIMER_SOURCE="${CHECK_TIMER_SOURCE:-$REPO_ROOT/sms-modem-check.timer}"
```

After the existing `install_modem_reattach()` function, add:

```bash
# The checker runs from the repo checkout; only the units are installed, with
# ExecStart rewritten to this checkout's path.
install_modem_check() {
  local rendered
  rendered="$(mktemp)"
  sed "s|__REPO_ROOT__|$REPO_ROOT|g" "$CHECK_SERVICE_SOURCE" > "$rendered"
  sudo -- install -D -m 0644 "$rendered" "$SYSTEMD_UNIT_DIR/sms-modem-check.service"
  rm -f "$rendered"
  sudo -- install -D -m 0644 "$CHECK_TIMER_SOURCE" "$SYSTEMD_UNIT_DIR/sms-modem-check.timer"
  sudo -- systemctl daemon-reload
  sudo -- systemctl enable --now sms-modem-check.timer
}
```

In `main()`, add the call right after `install_modem_reattach`:

```bash
  create_host_dirs
  install_quadlet_unit
  install_modem_reattach
  install_modem_check
  restart_service
```

- [ ] **Step 5: Run to verify it passes**

Run: `uv run pytest tests/test_setup_script.py::test_setup_installs_modem_check_timer -v`
Expected: PASS

- [ ] **Step 6: Run the whole suite**

Run: `uv run pytest -q`
Expected: PASS, no failures. Adding a `systemctl enable` call must not break the
existing setup tests — they all stub `systemctl`.

- [ ] **Step 7: Commit**

```bash
git add sms-modem-check.service sms-modem-check.timer setup.sh tests/test_setup_script.py
git commit -m "feat: install modem SMS-service check timer"
```

---

### Task 6: Documentation

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: everything above.
- Produces: nothing code depends on.

- [ ] **Step 1: Add the troubleshooting section**

Append to `README.md`, after the environment-variable table:

```markdown
## Troubleshooting: no SMS arriving, but everything looks healthy

If the service is running, the queue is empty, the log shows no errors and the
modem reports good signal — but no SMS have arrived for hours or days — check
whether the modem still has **SMS service**, which is not the same thing as
having a signal.

SMS are delivered over the **circuit-switched** domain. A modem can be
registered for **packet-switched** service only: data works, the signal looks
fine, the SIM is valid, and no SMS can ever arrive. `gammu-smsd-monitor` cannot
see this — it reports no registration state at all.

The periodic check (`sms-modem-check.timer`) alerts to Telegram when this
persists. To inspect it by hand:

```bash
sudo journalctl -u sms-modem-check.service -n 20
```

A healthy line reads `srv_domain=3 sms_capable=True`.

`srv_domain` values, from `AT^SYSINFO`:

| Value | Meaning | SMS work? |
| ----- | ------- | --------- |
| 0 | No service | No |
| 1 | CS only | Yes |
| 2 | **PS only** — data but no SMS | **No** |
| 3 | CS+PS — normal | Yes |
| 4 | Registering | Not yet |

### Fixing it

Force the modem to re-register:

```bash
cd /path/to/sms-to-telegram && sudo ./host/sms_modem_reregister.py
```

This takes **1-3 minutes** and passes through `srv_domain=4` with **zero
signal** on the way. That looks like a failure but is the normal recovery path —
wait it out. It exits 0 once `srv_domain=3` is reached, or 1 on timeout.

It is never run automatically: it drops the modem off the network for the
duration, and if the network is genuinely refusing circuit-switched service,
retrying would inflict repeated outages without fixing anything. If it times
out twice, the problem is with the carrier or the SIM, not the modem.
```

- [ ] **Step 2: Verify the README renders and the suite still passes**

Run: `uv run pytest -q`
Expected: PASS. (`tests/test_runtime_contract.py` asserts on README content;
confirm the new section does not break its expectations.)

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: how to diagnose and fix loss of SMS service"
```
