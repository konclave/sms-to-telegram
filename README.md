# SMS to Telegram Forwarder

A Docker container that forwards SMS messages from a GSM modem to a Telegram chat using gammu-smsd.

## Features

- Forwards incoming SMS messages to a specified Telegram chat
- Supports any GSM modem compatible with gammu
- Local source builds support both Debian-based and Alpine-based container variants
- Configurable PIN code for SIM card
- File-backed queue with retrying worker delivery
- At-least-once delivery semantics for transient Telegram failures
- Built-in healthcheck for stuck queues and missing worker process
- Modem status monitoring with Telegram alerts for connection loss and low signal
- Supports multiple messages handling

## Prerequisites

- Docker installed on your system
- `uv` installed for local development
- Python 3.14 for local development and image builds
- GSM modem (Huawei or compatible)
- Telegram Bot Token
- Telegram Chat ID
- SIM card (with or without PIN)

## Local Development

The supported local workflow uses `uv` with Python 3.14.

1. Install the project environment:

```bash
uv sync
```

2. Run the test suite:

```bash
uv run pytest
```

`uv sync` installs the package and the runtime entrypoints used by the container image:

- `sms-forwarder-enqueue`
- `sms-forwarder-worker`
- `sms-forwarder-healthcheck`
- `sms-forwarder-modem-monitor`

## Quick Start

1. Create a Telegram bot using [@BotFather](https://t.me/botfather) and get the bot token
2. Get your Telegram chat ID (you can use [@userinfobot](https://t.me/userinfobot))
3. Pull and run the container:

```bash
docker run -d \
  --device=/dev/ttyUSB0:/dev/ttyUSB0 \
  -v /var/lib/sms-to-telegram-queue:/var/spool/sms-forwarder \
  -e DEVICE=/dev/ttyUSB0 \
  -e PIN=0000 \
  -e BOT_TOKEN=your_telegram_bot_token \
  -e CHAT_ID=your_telegram_chat_id \
  ghcr.io/<owner>/sms-to-telegram:v1.2.3
```

The queue volume is strongly recommended. Without it, pending retries are lost when the container is recreated.

## Environment Variables

| Variable | Description | Default |
| -------- | ----------- | ------- |
| DEVICE | Path to GSM modem device | Required |
| PIN | SIM card PIN code | 0000 |
| BOT_TOKEN | Telegram Bot API token | Required |
| CHAT_ID | Telegram chat ID to send messages to | Required |
| QUEUE_ROOT | Queue directory inside the container | `/var/spool/sms-forwarder` |
| WORKER_PID_FILE | Worker PID file used by the healthcheck | `/var/run/sms-forwarder-worker.pid` |
| ENQUEUE_LOG_PATH | Explicit log target for the enqueue hook | unset |
| MAX_ATTEMPTS | Maximum delivery attempts before moving a message to `failed/` | `24` |
| KEEP_SENT | Number of delivered message files to retain | `1000` |
| KEEP_FAILED | Number of failed message files to retain | `1000` |
| WORKER_IDLE_SLEEP_SECONDS | Worker sleep interval when no due messages exist | `5` |
| QUEUE_HEALTH_MAX_AGE_SECONDS | Healthcheck threshold for oldest due pending item | `300` |
| GAMMU_CONFIG | Path to gammu config used by the modem monitor | `/etc/gammurc` |
| SIGNAL_WARN_THRESHOLD | Signal strength % below which the modem monitor sends a low-signal alert | `20` |
| MONITOR_INTERVAL_SECONDS | How often the modem monitor polls `gammu-smsd-monitor` | `60` |
| UNREACHABLE_ALERT_AFTER | Consecutive polls with no modem before an unreachable alert is sent | `2` |
| SHUTDOWN_GRACE_SECONDS | How long the entrypoint waits for its children on stop before SIGKILLing them | `5` |

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

A healthy line reads `srv_domain=3 sms_capable=True failures=0 alert_active=False`.

`srv_domain` values, from `AT^SYSINFO`:

| Value | Meaning | SMS work? |
| ----- | ------- | --------- |
| 0 | No service | No |
| 1 | CS only | Yes |
| 2 | **PS only** — data but no SMS | **No** |
| 3 | CS+PS — normal | Yes |
| 4 | Registering | Not yet |

For a corroborating signal, `AT+CREG?` reports a separate `stat` value:
`0` means the modem is not registered **and not even searching** for a
network, `2` means it is searching, and `5` means it is registered while
roaming. `stat=0` alongside `srv_domain=0` is what the original incident
that motivated this check looked like — the modem was not merely between
registrations, it had given up looking.

### Fixing it

Force the modem to re-register:

```bash
cd /path/to/sms-to-telegram && sudo ./host/sms_modem_reregister.py
```

This takes **1-3 minutes** and passes through `srv_domain=4` with **zero
signal** on the way. That looks like a failure but is the normal recovery path —
wait it out. It exits 0 once the modem is SMS-capable (`srv_domain` 1 or 3),
or 1 on timeout.

While it runs, the forwarder service itself will likely log modem errors and
may restart one or more times during those 1-3 minutes — that is the same
loss of signal the handle warns about, seen from the other process. It
recovers on its own once circuit-switched service returns; it is not a sign
that the handle broke anything.

It is never run automatically: it drops the modem off the network for the
duration, and if the network is genuinely refusing circuit-switched service,
retrying would inflict repeated outages without fixing anything. If it times
out twice, the problem is with the carrier or the SIM, not the modem.

## Docker Images

The repository can be built locally from either `Dockerfile` or `Dockerfile.alpine`.

## Building from Source

To build the Debian-based image locally:

```bash
docker build -t sms-to-telegram .
```

To build the Alpine-based image locally:

```bash
docker build -f Dockerfile.alpine -t sms-to-telegram:alpine .
```

## GHCR Publishing

GitHub Actions publishes only the Alpine image, and it builds that release from `Dockerfile.alpine`.

Publishing is triggered by pushed version tags like `v1.2.3`. Each release is pushed to `ghcr.io/<owner>/sms-to-telegram` with both the original `v1.2.3` tag and the normalized `1.2.3` tag.

## Git Tags and Versioning

Release tags are created manually in `vX.Y.Z` form:

```bash
git tag v1.2.3
git push origin v1.2.3
```

Tagged releases build with that exact version. Non-tagged commits build as a development version derived from the most recent tag, so commits after `v1.2.3` resolve to a newer development version instead of reusing the release number.

Container builds need Git metadata during the install step so the package version can be derived correctly. The Dockerfiles mount `.git` only for that build step; the final runtime image does not include `.git`.

## How It Works

1. The container uses gammu-smsd to monitor the GSM modem for incoming messages
2. When a new SMS is received, gammu-smsd triggers the installed `sms-forwarder-enqueue` command
3. The hook persists each message into a local queue immediately
4. The installed `sms-forwarder-worker` command drains the queue, retries transient Telegram failures, and records failed deliveries
5. Messages include the sender's phone number and the message text

The runtime image also uses the installed `sms-forwarder-healthcheck` command for container health reporting instead of invoking loose repository scripts directly.

The `sms-forwarder-modem-monitor` command runs alongside the worker and gammu-smsd. It periodically polls `gammu-smsd-monitor` and sends a Telegram message when the modem loses network registration or signal drops below the configured threshold, and a follow-up message when conditions recover.

## Queue Operations

- Persist `/var/spool/sms-forwarder` if you want retries to survive container recreation.
- Inspect `pending/` for backlog, `failed/` for operator action, and `sent/` for recent delivery history.
- The container healthcheck fails if the worker is missing or due messages are stuck too long.

Queue layout:

- `pending/` holds messages waiting for first delivery or retry
- `processing/` is a transient claim area used by the worker
- `sent/` keeps a bounded local delivery history
- `failed/` contains messages that exhausted retries or hit terminal Telegram errors

Retry schedule:

- attempt 1 retry after `30s`
- attempt 2 retry after `120s`
- attempt 3 retry after `600s`
- attempt 4 retry after `1800s`
- later retries every `3600s`

Terminal Telegram failures such as invalid bot credentials or invalid chat targets are moved directly to `failed/`.

## Healthcheck

The image exposes a healthcheck that verifies:

- the worker PID file exists
- the worker process is still alive
- the oldest due item in `pending/` is not older than `QUEUE_HEALTH_MAX_AGE_SECONDS`

This catches the common case where the container is still running but delivery is stalled.

## Quadlet / Podman

The provided `sms-to-telegram.container` file expects a persistent queue mount similar to:

```ini
Volume=/var/lib/sms-to-telegram-queue:/var/spool/sms-forwarder:Z
```

If you use Quadlet, keep `QUEUE_ROOT=/var/spool/sms-forwarder` and mount that path persistently.

Typical host setup:

1. Install the Quadlet file at `/etc/containers/systemd/sms-to-telegram.container`
2. Create the queue directory on the host:

```bash
sudo mkdir -p /var/lib/sms-to-telegram-queue
```

3. Create the environment file referenced by the unit:

```bash
sudo tee /etc/systemd-notify.env >/dev/null <<'EOF'
PIN=0000
BOT_TOKEN=your_telegram_bot_token
CHAT_ID=your_telegram_chat_id
EOF
```

4. Verify the modem mapping in the unit matches your system. The default file uses:

```ini
Environment=DEVICE=/dev/ttyUSB0
AddDevice=/dev/serial/by-id/usb-HUAWEI_Technologies_HUAWEI_Mobile-if00-port0:/dev/ttyUSB0
```

5. Reload systemd and start the service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now sms-to-telegram.service
```

6. Check service state and logs:

```bash
sudo systemctl status sms-to-telegram.service
sudo journalctl -u sms-to-telegram.service -f
```

## Local Quadlet Deploy Flow

`setup.sh` is the local deploy entrypoint for the Quadlet service.

It:

1. computes a fingerprint from image-relevant files
2. for a `localhost/` image: checks whether it exists and rebuilds only when missing or the fingerprint changed; for a remote image such as `ghcr.io/...`, the build step is skipped entirely
3. installs `sms-to-telegram.container` into `/etc/containers/systemd/`
4. reloads systemd and restarts `sms-to-telegram.service`
5. records local deploy state in `.deploy/sms-to-telegram-state.json`

Fingerprint inputs include packaging and runtime files such as `pyproject.toml`, `uv.lock`, `.python-version`, `entrypoint.sh`, `gammurc`, and the `sms_forwarder/` package.

The local deploy fingerprint also tracks Git version state, so new commits, tags, dirty working trees, and other version-affecting Git changes can trigger a rebuild even when the tracked application files themselves are unchanged.

The provided `sms-to-telegram.container` uses the GHCR image published by GitHub Actions:

```ini
Image=ghcr.io/konclave/sms-to-telegram:latest
```

To build and deploy a local image instead, override `IMAGE_NAME` before running `setup.sh`:

```bash
IMAGE_NAME=localhost/sms-to-telegram:latest bash setup.sh
```

Typical output when deploying a remote image:

- `build skipped: remote image ghcr.io/...`
- `deployed image: sha256:...`

Typical output when deploying a local image (`localhost/sms-to-telegram:latest`):

- `build skipped: fingerprint unchanged`
- `build triggered: image missing`
- `build triggered: source fingerprint changed`
- `deployed image: sha256:...`

The `.deploy/` directory is local-only and gitignored. It is used to track the last built image ID, source fingerprint, and deploy timestamps for this machine.

The provided `sms-to-telegram.container` is the reusable Quadlet template and reads secrets from `/etc/systemd-notify.env`.

## Modem Monitoring

The container runs `sms-forwarder-modem-monitor` as a background process. It polls `gammu-smsd-monitor` on a configurable interval and sends Telegram alerts when modem health changes.

**Alerts sent:**

| Condition | Message |
| --------- | ------- |
| Network registration lost | `Modem: network connection lost (state: searching)` |
| Network registration restored | `Modem: network connection restored (state: home)` |
| Signal below threshold | `Modem: signal low (12% — below threshold 20%)` |
| Signal recovered | `Modem: signal recovered (25%)` |
| `gammu-smsd-monitor` failing | `Modem: monitor tool failing — exit_code=1 ...` |
| `gammu-smsd-monitor` recovered | `Modem: monitor tool recovered` |

Alerts fire only on state transitions — if the modem stays disconnected across multiple poll cycles, only one alert is sent. A recovery message is sent when the condition clears.

**Log events** emitted to stdout:

- `event=monitor_startup` — logged once on process start with active config values
- `event=monitor_poll` — logged every cycle with current signal %, network state, and alert flags
- `event=monitor_error` — logged when `gammu-smsd-monitor` fails

If `gammu-smsd-monitor` is not installed or consistently fails, the monitor sends one alert and backs off — it does not flood Telegram.

## Modem Re-enumeration Recovery

The Huawei modem drops off the USB bus on its own and does not come back on the same tty name (`ttyUSB0` → `ttyUSB1` → …). `AddDevice` resolves the by-id symlink once, at container creation, so the running container is left holding a dead device node — only a restart reattaches it.

`99-sms-modem-reattach.rules` triggers `sms-modem-reattach.service` when the `if00` port reappears. That unit does **not** restart immediately: re-enumeration comes in bursts, and the modem often drops again within a few seconds. `wait-for-modem-device.sh` (installed to `/usr/local/lib/sms-to-telegram/`) first waits for the device node to stay present continuously before the restart is issued:

| Variable | Default | Meaning |
| -------- | ------- | ------- |
| `MODEM_DEVICE` | the by-id symlink | Device node to watch |
| `MODEM_STABLE_SECONDS` | `45` | How long it must stay present |
| `MODEM_WAIT_TIMEOUT` | `300` | Give up after this long |
| `MODEM_POLL_INTERVAL` | `1` | Seconds between checks |

This matters for alerting. A restart issued while the device is missing makes podman fail on the by-id symlink (`status=125`), and systemd reports that as a unit failure — which `OnFailure=quadlet-notify@` turns into a Telegram alert. Waiting for a stable device keeps a burst of re-enumerations to one restart instead of one per bounce, and keeps restarts from being issued into a missing device.

**The window must be read against the modem's measured flap period.** The original `8s` was chosen against "drops again within a few seconds", but a misbehaving modem drops roughly every **26s** — so the window was satisfied by nearly every bounce. A restart was then issued into a device with about 10s left to live, and a container start takes 5–10s. Measured over 24h that produced:

| | per 24h |
| --- | --- |
| Kernel USB events | 7743 |
| `sms-modem-reattach.service` runs | 1677 |
| `sms-to-telegram.service` failures | 1079 |
| Telegram alerts | 1079 |

A window *longer* than the flap period cannot be satisfied by a flapping modem at all, so no restart is issued while the modem is unusable — and the one that is issued, once it genuinely settles, lands on a device that survives the start. `MODEM_WAIT_TIMEOUT` is correspondingly long so the oneshot keeps waiting across several flap cycles rather than giving up on the first; `TimeoutStartSec` in the unit must stay above it so the helper's own timeout is what fires.

## Failure Alert Throttling

`systemd-notify-on-failure.sh` (installed to `/usr/local/lib/systemd-notify/on-failure.sh`) is the `OnFailure=quadlet-notify@` handler shared by every quadlet on the host. It alerts on a *transition* into failure, not on every failure.

The original version sent unconditionally. That assumes failures are rare; for `sms-to-telegram` a failing modem makes failure the steady state, and the script turned one hardware fault into 2396 Telegram messages — enough to bury every real message in the channel.

| Variable | Default | Meaning |
| -------- | ------- | ------- |
| `NOTIFY_COOLDOWN_SECONDS` | `3600` | Minimum gap between alerts for one unit |
| `NOTIFY_STATE_DIR` | `/var/lib/systemd-notify` | Per-unit cooldown state |
| `NOTIFY_LOG` | `/var/log/quadlet-failures.log` | Local audit trail |
| `NOTIFY_ENV_FILE` | `/etc/systemd-notify.env` | `BOT_TOKEN` / `CHAT_ID` |

Within the cooldown a failure is counted, not sent; the next alert past the window carries `⏸ N further failures suppressed in the previous M min`. Each unit is throttled independently, and **the local log still records every failure** — only the Telegram send is throttled, so a suppressed alert stays recoverable from the host. At the rate measured above this turns 1079 alerts a day into 24.

`entrypoint.sh` runs as the container's PID 1. PID 1 is exempt from default signal actions — a signal with no handler installed is simply discarded — so the entrypoint installs an explicit `TERM`/`INT` trap. Without it podman's `StopSignal` is ignored, the stop times out into `SIGKILL`, and systemd records `status=137/n/a` and fires `OnFailure`, making every deliberate restart look like a crash.

The trap alone is not enough. `gammu-smsd` blocked on a dead USB tty never acts on `SIGTERM`, and because the modem re-enumerates constantly that is the state a restart usually finds it in. Waiting on it without a bound only moves the stall to podman's stop timeout, producing the same `status=137`. The teardown therefore waits `SHUTDOWN_GRACE_SECONDS` (default 5), then `SIGKILL`s the children and exits 0 on its own terms — exiting PID 1 tears down the container's PID namespace, so even a child stuck in uninterruptible sleep cannot hold the stop open. A child dying on its own still falls through to `exit 1`, so genuine failures keep alerting.

## Troubleshooting

1. Make sure your GSM modem is properly connected and recognized by the system
2. Check if the correct device path is provided in the DEVICE environment variable
3. Verify that the SIM card PIN is correct if PIN protection is enabled
4. Ensure the Telegram bot token is valid and the bot has permission to send messages
5. Confirm that the chat ID is correct and the bot is a member of the chat
6. Inspect the queue volume if messages are stuck in `pending/` or `failed/`
7. Check container logs for `event=worker_startup`, `event=delivery_retry`, `event=delivery_failed`, and `event=delivery_success`; modem monitor events are `event=monitor_startup`, `event=monitor_poll`, and `event=monitor_error`
8. If messages stay in `processing/` after a crash, restart the container so the worker can recover them back into `pending/`
9. A burst of `Quadlet Failure Alert` messages with `status=137/n/a` means the container is being SIGKILLed on stop rather than exiting cleanly — check that `entrypoint.sh` still traps `TERM`. Alerts with `status=125` and `stat /dev/serial/by-id/...: no such file or directory` mean a restart was issued while the modem was off the bus; check `journalctl -u sms-modem-reattach.service` and `journalctl -k | grep -c 'USB disconnect'`
