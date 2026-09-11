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

sms-forwarder-worker &
worker_pid=$!
printf '%s\n' "$worker_pid" > "$WORKER_PID_FILE"

gammu-smsd -c /etc/gammurc -p /var/run/gammu-smsd.pid &
gammu_pid=$!

sms-forwarder-modem-monitor &
monitor_pid=$!

# This script is the container's PID 1, and PID 1 is exempt from default signal
# actions: a signal with no handler installed is discarded. Without this trap
# podman's StopSignal is ignored, the stop times out into SIGKILL, and systemd
# records status=137 and fires OnFailure -- so every deliberate restart pages
# the operator as if the service had crashed.
SHUTDOWN_GRACE_SECONDS=${SHUTDOWN_GRACE_SECONDS:-5}

children_running() {
  kill -0 "$worker_pid" 2>/dev/null \
    || kill -0 "$gammu_pid" 2>/dev/null \
    || kill -0 "$monitor_pid" 2>/dev/null
}

terminate() {
  trap - TERM INT
  kill "$worker_pid" "$gammu_pid" "$monitor_pid" 2>/dev/null || true

  # gammu-smsd blocked on a dead USB tty never acts on SIGTERM, and the modem
  # re-enumerates constantly, so that is the state a restart usually finds it
  # in. Waiting on it without a bound just moves the stall: podman's stop
  # timeout expires, PID 1 is SIGKILLed, and systemd records status=137 --
  # which is the crash alert this trap exists to prevent. Give the children a
  # short grace period, then stop waiting on them.
  waited=0
  while [ "$waited" -lt "$SHUTDOWN_GRACE_SECONDS" ] && children_running; do
    sleep 1 &
    wait "$!" 2>/dev/null || true
    waited=$((waited + 1))
  done
  kill -KILL "$worker_pid" "$gammu_pid" "$monitor_pid" 2>/dev/null || true

  # Exiting here tears down the container's PID namespace, so a child still
  # stuck in uninterruptible sleep cannot hold the stop open either.
  exit 0
}
trap terminate TERM INT

while kill -0 "$worker_pid" 2>/dev/null \
      && kill -0 "$gammu_pid" 2>/dev/null \
      && kill -0 "$monitor_pid" 2>/dev/null; do
  # A foreground sleep would defer the trap until it returns; waiting on a
  # background one is interrupted by the signal instead.
  sleep 1 &
  wait "$!" 2>/dev/null || true
done

kill "$worker_pid" "$gammu_pid" "$monitor_pid" 2>/dev/null || true
wait "$worker_pid" "$gammu_pid" "$monitor_pid" 2>/dev/null || true
exit 1
