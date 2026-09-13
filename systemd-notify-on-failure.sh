#!/bin/bash
# Telegram alert for a systemd unit that entered the failed state, invoked as
# OnFailure=quadlet-notify@%n.service.
#
# This alerts on a *transition* into failure, not on every failure. The design
# originally assumed failures are rare; for sms-to-telegram they are the steady
# state -- the modem re-enumerates about once a minute, each reappearance
# restarts the container, and roughly two thirds of those restarts lose the
# device again mid-start. That turned one hardware fault into 2396 Telegram
# messages. A cooldown per unit collapses a run of failures into one alert that
# carries the count, so the channel still reports the fault without burying
# every other message in the channel.
set -euo pipefail

SERVICE="${1:?usage: on-failure.sh <unit>}"
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
NOW=$(date '+%s')

LOG=${NOTIFY_LOG:-/var/log/quadlet-failures.log}
ENV_FILE=${NOTIFY_ENV_FILE:-/etc/systemd-notify.env}
STATE_DIR=${NOTIFY_STATE_DIR:-/var/lib/systemd-notify}
COOLDOWN=${NOTIFY_COOLDOWN_SECONDS:-3600}

# Load credentials
# shellcheck source=/dev/null
source "$ENV_FILE"

# The local log is the audit trail and stays complete: only the Telegram send
# is throttled, so a suppressed alert is still recoverable from the host.
echo "[${TIMESTAMP}] FAILED: ${SERVICE}" >> "$LOG"

# A unit name reaches us as a systemd instance name, which may legitimately
# contain characters that are not safe in a path. Fold anything outside the
# allowed set so the state file cannot escape STATE_DIR.
slug=$(printf '%s' "$SERVICE" | tr -c 'A-Za-z0-9._@-' '_')
state_file="$STATE_DIR/$slug"
mkdir -p "$STATE_DIR"

# systemd serialises activations of one quadlet-notify@ instance and each unit
# gets its own state file, so concurrent read-modify-write of the same file
# does not arise. The temp-file rename only guards against a torn write.
last_sent=0
suppressed=0
suppressed_since=0
if [ -r "$state_file" ]; then
  read -r last_sent suppressed suppressed_since < "$state_file" || true
fi
for var in last_sent suppressed suppressed_since; do
  case "${!var}" in
    '' | *[!0-9]*) printf -v "$var" '%s' 0 ;;
  esac
done

write_state() {
  local tmp="$state_file.tmp.$$"
  printf '%s %s %s\n' "$1" "$2" "$3" > "$tmp"
  mv "$tmp" "$state_file"
}

if [ "$last_sent" -ne 0 ] && [ "$((NOW - last_sent))" -lt "$COOLDOWN" ]; then
  if [ "$suppressed" -eq 0 ]; then
    suppressed_since=$NOW
  fi
  write_state "$last_sent" "$((suppressed + 1))" "$suppressed_since"
  exit 0
fi

# Grab last 10 log lines from the failed service
JOURNAL=$(journalctl -u "$SERVICE" -n 10 --no-pager --output=cat 2>/dev/null || echo "No logs available")

# Escape HTML special characters in dynamic content
escape_html() {
  echo "$1" | sed 's/&/\&amp;/g; s/</\&lt;/g; s/>/\&gt;/g'
}

SERVICE_ESC=$(escape_html "$SERVICE")
HOST_ESC=$(escape_html "$(hostname)")
JOURNAL_ESC=$(escape_html "$JOURNAL")

# Absolute times would need date's non-portable epoch formatting; a span reads
# better in a notification anyway.
SUPPRESSED_NOTE=""
if [ "$suppressed" -gt 0 ]; then
  span_minutes=$(( (NOW - suppressed_since) / 60 ))
  SUPPRESSED_NOTE="
<b>⏸ ${suppressed} further failures suppressed</b> in the previous ${span_minutes} min"
fi

# Build message
MESSAGE="🔴 <b>Quadlet Failure Alert</b>

<b>Service:</b> <code>${SERVICE_ESC}</code>
<b>Host:</b> <code>${HOST_ESC}</code>
<b>Time:</b> ${TIMESTAMP}${SUPPRESSED_NOTE}

<b>Last logs:</b>
<pre>${JOURNAL_ESC}</pre>"

# Send to Telegram
curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
  --data-urlencode "chat_id=${CHAT_ID}" \
  --data-urlencode "text=${MESSAGE}" \
  --data-urlencode "parse_mode=HTML" \
  >> "$LOG" 2>&1

write_state "$NOW" 0 0
