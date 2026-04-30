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

## How It Works

1. The container uses gammu-smsd to monitor the GSM modem for incoming messages
2. When a new SMS is received, gammu-smsd triggers the installed `sms-forwarder-enqueue` command
3. The hook persists each message into a local queue immediately
4. The installed `sms-forwarder-worker` command drains the queue, retries transient Telegram failures, and records failed deliveries
5. Messages include the sender's phone number and the message text

The runtime image also uses the installed `sms-forwarder-healthcheck` command for container health reporting instead of invoking loose repository scripts directly.

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
2. checks whether `localhost/sms-to-telegram:latest` already exists
3. rebuilds only when the image is missing or the fingerprint changed
4. installs `sms-to-telegram.container` into `/etc/containers/systemd/`
5. reloads systemd and restarts `sms-to-telegram.service`
6. records local deploy state in `.deploy/sms-to-telegram-state.json`

Fingerprint inputs include packaging and runtime files such as `pyproject.toml`, `uv.lock`, `.python-version`, `entrypoint.sh`, `gammurc`, and the `sms_forwarder/` package. Documentation-only edits do not trigger a rebuild.

Typical output:

- `build skipped: fingerprint unchanged`
- `build triggered: image missing`
- `build triggered: source fingerprint changed`
- `deployed image: sha256:...`

The `.deploy/` directory is local-only and gitignored. It is used to track the last built image ID, source fingerprint, and deploy timestamps for this machine.

The Quadlet unit is expected to reference the stable local image:

```ini
Image=localhost/sms-to-telegram:latest
```

The provided `sms-to-telegram.container` is the reusable Quadlet template and reads secrets from `/etc/systemd-notify.env`.

## Troubleshooting

1. Make sure your GSM modem is properly connected and recognized by the system
2. Check if the correct device path is provided in the DEVICE environment variable
3. Verify that the SIM card PIN is correct if PIN protection is enabled
4. Ensure the Telegram bot token is valid and the bot has permission to send messages
5. Confirm that the chat ID is correct and the bot is a member of the chat
6. Inspect the queue volume if messages are stuck in `pending/` or `failed/`
7. Check container logs for `event=worker_startup`, `event=delivery_retry`, `event=delivery_failed`, and `event=delivery_success`
8. If messages stay in `processing/` after a crash, restart the container so the worker can recover them back into `pending/`
