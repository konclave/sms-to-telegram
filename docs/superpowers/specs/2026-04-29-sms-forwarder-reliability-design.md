# SMS Forwarder Reliability Design

## Context

The current container receives SMS messages with `gammu-smsd` and forwards them to Telegram through `/etc/sms_to_telegram.sh`.

That design is brittle in two ways:

- The shell hook uses `eval`, so ordinary SMS content can break the script or be interpreted as shell syntax.
- Telegram delivery happens directly in the receive hook, so network failures are coupled to SMS ingestion.

The goal of this design is to improve delivery reliability first and operations second, while keeping the deployment model close to the existing single-container setup.

## Goals

- Accept arbitrary SMS content without shell quoting failures.
- Decouple SMS receipt from Telegram delivery.
- Provide `at-least-once` delivery with local retries.
- Make pending, failed, and delivered messages inspectable on disk.
- Keep the existing Podman/quadlet deployment shape.

## Non-Goals

- Exactly-once Telegram delivery.
- Horizontal scaling or multi-container coordination.
- Replacing `gammu-smsd` or changing modem integration.
- Adding an external queue or database service.

## Recommended Approach

Replace the shell forwarding hook with a small Python enqueue program and add a separate long-running Python worker in the same container.

The receive hook will persist each SMS into a local file-backed queue and return immediately. The worker will own Telegram API calls, retries, backoff, logging, and queue state transitions.

This keeps ingress simple and deterministic while isolating the unreliable network path behind a durable local queue.

## Architecture

### Components

1. `gammu-smsd`
   Receives inbound SMS and invokes `RunOnReceive`.

2. Python enqueue hook
   Reads the SMS metadata from environment variables, writes one queue item per message into `pending/`, and exits.

3. Python sender worker
   Runs continuously, claims due queue items, sends them to Telegram, and updates queue state.

4. File-backed queue
   Stores queue items under a spool directory inside the container, optionally backed by a persistent volume.

### Queue Layout

Use a dedicated directory tree such as:

- `/var/spool/sms-forwarder/pending`
- `/var/spool/sms-forwarder/processing`
- `/var/spool/sms-forwarder/sent`
- `/var/spool/sms-forwarder/failed`

Each message is stored as a single JSON file. File names should include a timestamp and random suffix to avoid collisions and to keep directory listings roughly ordered by arrival time.

### Queue Item Format

Each JSON file should include:

- `id`
- `received_at`
- `sender`
- `text`
- `attempts`
- `next_attempt_at`
- `last_error`
- `telegram_chat_id`

Optional fields may include modem metadata if `gammu-smsd` exposes it and it is useful for debugging.

## Data Flow

### Enqueue Path

1. `gammu-smsd` invokes the Python hook through `RunOnReceive`.
2. The hook reads `SMS_MESSAGES` and the per-message environment variables.
3. For each message, the hook writes a complete JSON payload into a temporary file.
4. The hook atomically renames the temporary file into `pending/`.
5. The hook exits successfully once all files are persisted locally.

The enqueue hook must not call Telegram and must not contain retry logic.

### Delivery Path

1. The worker scans `pending/` for items whose `next_attempt_at` is due.
2. The worker claims one item by atomically moving it into `processing/`.
3. The worker posts the message to Telegram using a structured JSON request.
4. On success, the worker moves the file into `sent/`.
5. On retryable failure, the worker increments `attempts`, sets a new `next_attempt_at`, records `last_error`, and moves the file back to `pending/`.
6. On terminal failure, the worker records `last_error` and moves the file into `failed/`.

If the worker crashes after Telegram accepts the request but before the state move completes, the item may be retried and delivered twice. This is acceptable because the required semantic is `at-least-once`.

## Retry Policy

Use exponential backoff with an upper bound. The exact schedule can be configurable, but the initial default should be:

- 30 seconds
- 2 minutes
- 10 minutes
- 30 minutes
- 60 minutes for subsequent attempts

Retryable failures:

- DNS failures
- connection errors
- timeouts
- HTTP `429`
- HTTP `5xx`

Terminal failures:

- invalid bot token
- invalid chat ID
- malformed request rejected as client error

The worker should make terminal-versus-retryable decisions from both the HTTP status code and the Telegram response body when available.

## Logging And Operations

Both Python programs should log to stdout/stderr as single-line structured messages that are readable in `journalctl`.

Log events should include at minimum:

- enqueue success
- enqueue failure
- delivery success
- delivery retry
- delivery terminal failure
- worker startup
- stale item recovery on startup

Operational model:

- `pending/` shows backlog waiting to be sent.
- `processing/` should normally be near-empty.
- `failed/` contains items needing operator action.
- `sent/` provides a local delivery audit trail until rotation or cleanup.

Retention should be explicit so disk usage stays bounded. `sent/` and `failed/` items should either be cleaned up by age or capped by count in a later implementation detail, with conservative defaults.

## Startup And Lifecycle

The container entrypoint should:

1. render configuration as it does today
2. ensure queue directories exist
3. move any stale items from `processing/` back to `pending/`
4. start the Python worker
5. start `gammu-smsd` in the foreground

The container lifecycle should remain anchored to `gammu-smsd`, but the worker must be treated as a required sidecar process within the same container. If the worker exits unexpectedly, the container should fail or be restarted rather than silently continuing without delivery.

The deployment should also expose a health signal that can detect a stalled queue, not just live processes. A simple first version is to report unhealthy if the worker is absent or if the oldest due item in `pending/` exceeds a configured age threshold.

## Persistence

If the queue directory is ephemeral, pending retries are lost when the container is recreated. For reliable operations, the queue directory should be backed by a persistent volume or bind mount.

This persistence is strongly recommended because it protects queued messages during image updates, host restarts, and container replacement.

## Security And Robustness

- Remove all use of `eval`.
- Build Telegram requests with Python JSON serialization rather than shell interpolation.
- Keep bot token and chat ID in environment or file-based secrets as today unless deployment changes later.
- Sanitize logs so message bodies can be included intentionally, not accidentally through exception traces.

## Testing Strategy

### Unit Tests

- queue item creation
- atomic enqueue behavior
- retry scheduling
- Telegram error classification
- stale `processing/` recovery

### Integration Tests

- enqueue from representative `gammu-smsd` environment variables
- send success against a mocked Telegram endpoint
- retry on network failure
- move to `failed/` on terminal API error
- preserve messages across worker restart

### Manual Verification

- send SMS containing quotes, apostrophes, backslashes, and newlines
- stop network access temporarily and confirm queued retries accumulate
- restore network access and confirm backlog drains
- inspect `journalctl` output for readable event logs

## Migration Scope

Implementation should be limited to:

- replacing the shell forwarding hook with a Python enqueue script
- adding the Python sender worker
- updating the container image to include Python
- creating queue directories
- updating the entrypoint to manage the worker lifecycle
- documenting the queue layout and troubleshooting flow

No podlet redesign is required for the first iteration beyond any volume mount needed for queue persistence.
