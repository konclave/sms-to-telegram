# Modem CS-Registration Monitoring and Recovery

Date: 2026-09-10
Status: Approved

## Problem

On 2026-09-10 the forwarder stopped delivering SMS while every indicator
available to it looked healthy: 39% signal, a valid unlocked SIM, a stable
IMEI, gammu connected, zero errors in the log, an empty queue.

The modem was registered on the **packet-switched** domain only. SMS are
delivered over the **circuit-switched** domain. Nothing could arrive, and
nothing in the system could see why.

Evidence captured from the modem's diagnostic port at the time:

```
^SYSINFO: 2,2,1,3,1,0,3     srv_domain 2 = PS only
+CREG:    2,0,37A,A228      stat 0 = not registered, not searching
+CGREG:   2,5,37A,A228      stat 5 = registered, roaming (packet only)
+CPMS:    "ME",0,255,...    zero messages ever stored
```

After forcing re-registration with `AT+COPS=0`:

```
^SYSINFO: 2,3,1,3,1,0,3     srv_domain 3 = CS+PS
+CREG:    2,5,37A,A228      stat 5 = registered, roaming
```

Buffered messages flushed within seconds and delivery resumed.

The blind spot is structural. `gammu-smsd-monitor` reports signal, IMEI and
counters, but **no registration state at all** — the same gap that previously
hid a non-functional parser. Signal strength is not evidence of SMS capability,
and the system currently has no way to tell the difference.

The SIM is Russian (SMSC `+79168999100`, MTS) roaming on Vodafone.de, which is
plausibly why the CS attach is fragile. Recurrence should be assumed.

## Goals

1. Detect sustained loss of circuit-switched service and alert to Telegram.
2. Provide a handle that forces re-registration, runnable by a human.
3. Document the symptom, the diagnosis, and the fix.

## Non-goals

- **Automatic recovery.** `AT+COPS=0` drops the modem off the network for
  roughly 2.5 minutes. If the network is genuinely refusing CS service, an
  automatic retry loop would inflict repeated outages while fixing nothing.
  Manual only, by decision.
- Replacing the existing `gammu-smsd-monitor` polling. The new check is
  additive so that a missing diagnostic port degrades to today's behaviour
  rather than blinding the monitor entirely.
- Any change to the container, the image, or the quadlet.

## Placement

Everything runs **on the host**, not in the container.

The deciding argument is availability: `podman exec` requires a running
container, so a container-hosted handle would be unavailable precisely when the
modem is in a bad enough state to keep the container down. The AT layer is also
host-level in nature — it concerns the modem, not SMS forwarding — and this
matches the udev rule and reattach unit already installed host-side.

Consequence: no second `AddDevice`, no quadlet change, no image rebuild.

## Components

All scripts live in `host/` and **run from the repo checkout**. Nothing copies
them elsewhere. `git pull` is the update path, which removes the class of drift
that caused the deployed quadlet to silently diverge from the repo.

All three are `.py` modules with a `#!/usr/bin/env python3` shebang and the
executable bit, run directly as `./host/<name>.py`. They are deliberately not
hyphenated command-style names: hyphens are not importable, and every piece of
logic here has to be reachable from the test suite. Each entry point guards its
`main()` behind `if __name__ == "__main__":` so importing it has no side effects.

Both entry points do `sys.path.insert(0, dirname(__file__))` before
`import sms_modem_at`, so the same plain import name works whether the file is
executed directly or imported by a test. `tests/conftest.py` adds `host/` to
`sys.path` for the test side; the repo currently has no conftest, so this is a
new file.

### `host/sms_modem_at.py`

Stdlib-only AT client and parser.

- `query(port, commands, *, settle=1.2) -> str` — opens the port, writes each
  command, returns the accumulated reply text.
- `parse_sysinfo(text) -> ServiceStatus` — parses `^SYSINFO:` into
  `srv_status, srv_domain, roam_status, sys_mode, sim_state`.
- `ServiceStatus.sms_capable` — `srv_domain in (1, 3)`, i.e. CS present.

Two requirements that are easy to get wrong and must be tested:

- **`HUPCL` must be cleared** before use. Otherwise closing the port drops DTR
  and can reset the modem — a checker polling every five minutes would be
  resetting the hardware all day. This is the `-hupcl` used in the manual
  probes.
- **The port is addressed by its by-id path**
  (`/dev/serial/by-id/usb-HUAWEI_Technologies_HUAWEI_Mobile-if01-port0`), never
  `/dev/ttyUSB2`. That name changes on every re-enumeration — the exact trap
  behind the original 13-day outage.

Baud rate is not set: the device rejects `stty 115200` and the rate is
meaningless on a USB serial port.

### `host/sms_modem_check.py`

Timer-driven checker. Read-only AT commands.

Behaviour per run:

1. If the by-id path is absent, exit 0 silently. Modem *absence* is already
   covered by the udev rule and `OnFailure=`; this check is only about a modem
   that is present but not CS-registered.
2. Query `AT^SYSINFO`, parse, evaluate `sms_capable`.
3. Update the debounce state and send alerts as described below.

### `host/sms_modem_reregister.py`

The handle. The only component that writes to the modem.

1. `AT+COPS=2` — deregister. This firmware answers `ERROR`; that is expected
   and must be tolerated, not treated as failure.
2. `AT+COPS=0` — re-register, automatic selection.
3. Poll `AT^SYSINFO` until `srv_domain == 3` or a timeout (default 180s),
   printing each reading.

Progress output is a requirement, not a nicety: recovery passes through
`srv_domain 4` and `CSQ 0,99` (no signal) for a minute or more, which looks
exactly like failure. A silent tool would be abandoned mid-recovery.

Exit 0 on restored CS service, 1 on timeout.

## Debounce state

The checker is a timer-driven oneshot, so unlike the in-process monitor it has
no memory between runs. State persists in `/var/lib/sms-modem-check/state.json`:

```json
{"consecutive_failures": 0, "alert_active": false}
```

Semantics mirror `AlertState` in `sms_forwarder/modem_monitor.py`:

- PS-only increments the counter; an alert fires once the counter reaches the
  threshold (default 2) and `alert_active` is false, which then latches.
- CS present resets the counter, and sends one recovery alert if latched.
- Otherwise silent.

Requiring two consecutive failures avoids alerting on a modem that is merely
mid-scan — the same reason the in-process monitor debounces.

Unreadable or corrupt state is treated as the default and rewritten. A missing
state file must never prevent the check from running.

## Alerting

The unit runs as root and reads `BOT_TOKEN` / `CHAT_ID` from
`/etc/systemd-notify.env` — already present for `on-failure.sh`, so no
credentials are duplicated. Delivery uses stdlib `urllib`; no new dependency.

Alert text names the condition and the remedy, for example:

```
Modem: no SMS service — registered on packet-switched domain only
(srv_domain=2). Data works; SMS cannot arrive.
Fix: cd <repo> && sudo ./host/sms_modem_reregister.py
```

## Installation

`setup.sh` installs **only two unit files**; no Python is copied.

- `sms-modem-check.service` — oneshot invoking the repo checker.
- `sms-modem-check.timer` — every 5 minutes. The condition is rare and
  slow-moving and each query costs about a second.

`ExecStart` is templated to the real `$REPO_ROOT` at install time, so the units
point back at the checkout. The handle needs no installation at all.

Accepted trade-off: root then executes a file inside a directory `konclave` can
write to. This is not a new exposure — `setup.sh` is already run with sudo from
that same directory — but it is a real property of the design and is recorded
here deliberately.

## Error handling

The check is strictly additive and must never make things worse:

- Missing device, permission error, timeout, or unparseable output → log and
  exit 0 without alerting. An alert would be a false alarm about the checker,
  not the modem.
- The checker never writes to the modem, so it cannot disturb gammu.
- gammu holds `if00`; the checker and handle use `if01`. Concurrent use of the
  two interfaces was verified safe by manual probing on 2026-09-10.

## Testing

Serial I/O is an injected transport callable, so no test touches hardware.

- **Parser**: the real captures from this incident —
  `^SYSINFO:2,2,1,3,1,0,3` must be `sms_capable == False`, and
  `^SYSINFO:2,3,1,3,1,0,3` must be `True`. Also `srv_domain 1` (CS only) is
  capable, `4` (searching) is not, and malformed input raises nothing.
- **`HUPCL` is cleared** — asserted against the termios flags the client sets,
  because getting this wrong silently resets the modem in production.
- **Debounce**: one failure is silent, two alert once, a third stays silent,
  recovery alerts once, alternating readings stay silent, and corrupt state
  recovers to the default.
- **Handle**: success, timeout, and the `AT+COPS=2 → ERROR` path.

Because these modules run under the host's Python 3.12 while the project
targets 3.14, their tests run under **both**:

```bash
uv run pytest tests/test_host_modem.py
uv run --python 3.12 pytest tests/test_host_modem.py
```

Without the second run, a 3.14-only construct would pass CI and fail only on
the host.

## Documentation

A README troubleshooting section covering:

- The symptom: service healthy, signal fine, no SMS for an extended period.
- How to read `srv_domain`, and that `CREG 0` means *not even searching*.
- How to pull the handle, and that a ~2.5 minute recovery through
  `srv_domain 4` and zero signal is normal.
- The headline: **signal strength is not evidence of SMS capability.**

## Risks

1. **The modem may not be the cause.** If the network is genuinely refusing CS
   service to this roaming SIM, the handle will not help and the answer lies
   with the carrier. The tool reports honestly on timeout rather than retrying.
2. **`^SYSINFO` is Huawei-specific.** Replacing the modem with another vendor
   would require a different query. `AT+CREG?` is the standards-based fallback
   and is already captured by the parser tests as a secondary signal.
3. **Python version split.** Mitigated by the dual test run above, but it
   remains a trap for future edits to `host/`.
