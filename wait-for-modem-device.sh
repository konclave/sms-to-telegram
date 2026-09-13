#!/bin/sh
# Block until the modem's by-id device node has been present continuously for
# MODEM_STABLE_SECONDS, or fail after MODEM_WAIT_TIMEOUT.
#
# The modem re-enumerates in bursts: it reappears and drops off again a few
# seconds later. Restarting the forwarder on the first reappearance races the
# next disconnect -- podman then fails to stat the by-id symlink, systemd
# records the start as a unit failure, and the notifier turns that into an
# alert. Waiting for the device to hold still keeps a burst to one restart and
# keeps a restart from being issued into a missing device.
#
# The window has to be read against the modem's measured flap period. While it
# is misbehaving it drops roughly every 26s, and a container start takes about
# 5-10s: an 8s window was satisfied by nearly every bounce, so a restart was
# issued into a device with ~10s left to live. That produced ~1700 restarts and
# ~1080 failed starts a day, each one a Telegram alert. A window longer than
# the flap period cannot be satisfied by a flapping modem at all, so the
# forwarder is restarted when the modem has actually settled and not before.
set -u

DEVICE=${MODEM_DEVICE:-/dev/serial/by-id/usb-HUAWEI_Technologies_HUAWEI_Mobile-if00-port0}
STABLE_SECONDS=${MODEM_STABLE_SECONDS:-45}
WAIT_TIMEOUT=${MODEM_WAIT_TIMEOUT:-300}
POLL_INTERVAL=${MODEM_POLL_INTERVAL:-1}

# POSIX sh has no fractional arithmetic, so count polls rather than seconds.
polls_for() {
  awk -v span="$1" -v interval="$POLL_INTERVAL" \
    'BEGIN { n = int(span / interval); print (n < 1) ? 1 : n }'
}

needed=$(polls_for "$STABLE_SECONDS")
limit=$(polls_for "$WAIT_TIMEOUT")

stable=0
polls=0
while [ "$polls" -lt "$limit" ]; do
  # -e follows the symlink, so a stale link left behind by a disconnected
  # modem reads as absent -- which is what podman's AddDevice sees too.
  if [ -e "$DEVICE" ]; then
    stable=$((stable + 1))
    if [ "$stable" -ge "$needed" ]; then
      exit 0
    fi
  else
    stable=0
  fi
  polls=$((polls + 1))
  sleep "$POLL_INTERVAL"
done

echo "$DEVICE did not stay present for ${STABLE_SECONDS}s within ${WAIT_TIMEOUT}s" >&2
exit 1
