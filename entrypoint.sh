#!/bin/sh
set -eu

originalfile=/etc/gammurc
tmpfile=/etc/gammurc.tmp
QUEUE_ROOT=${QUEUE_ROOT:-/var/spool/sms-forwarder}
WORKER_PID_FILE=${WORKER_PID_FILE:-/var/run/sms-forwarder-worker.pid}
ENQUEUE_LOG_PATH=${ENQUEUE_LOG_PATH:-/proc/1/fd/1}

cp "$originalfile" "$tmpfile"
envsubst < "$originalfile" > "$tmpfile"
mv "$tmpfile" "$originalfile"

mkdir -p \
  "$QUEUE_ROOT"/pending \
  "$QUEUE_ROOT"/processing \
  "$QUEUE_ROOT"/sent \
  "$QUEUE_ROOT"/failed

export QUEUE_ROOT WORKER_PID_FILE ENQUEUE_LOG_PATH

/usr/bin/send_worker.py &
worker_pid=$!
printf '%s\n' "$worker_pid" > "$WORKER_PID_FILE"

gammu-smsd -c /etc/gammurc -p /var/run/gammu-smsd.pid &
gammu_pid=$!

while kill -0 "$worker_pid" 2>/dev/null && kill -0 "$gammu_pid" 2>/dev/null; do
  sleep 1
done

kill "$worker_pid" "$gammu_pid" 2>/dev/null || true
wait "$worker_pid" "$gammu_pid" 2>/dev/null || true
exit 1
