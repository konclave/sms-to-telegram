#!/bin/bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
STATE_DIR="${STATE_DIR:-$REPO_ROOT/.deploy}"
STATE_FILE="$STATE_DIR/sms-to-telegram-state.json"
IMAGE_NAME="${IMAGE_NAME:-ghcr.io/konclave/sms-to-telegram:latest}"
QUADLET_SOURCE="${QUADLET_SOURCE:-$REPO_ROOT/sms-to-telegram.container}"
QUADLET_DIR="${QUADLET_DIR:-/etc/containers/systemd}"
QUADLET_TARGET="$QUADLET_DIR/sms-to-telegram.container"
UDEV_RULE_SOURCE="${UDEV_RULE_SOURCE:-$REPO_ROOT/99-sms-modem-reattach.rules}"
UDEV_RULE_DIR="${UDEV_RULE_DIR:-/etc/udev/rules.d}"
REATTACH_UNIT_SOURCE="${REATTACH_UNIT_SOURCE:-$REPO_ROOT/sms-modem-reattach.service}"
SYSTEMD_UNIT_DIR="${SYSTEMD_UNIT_DIR:-/etc/systemd/system}"
DEVICE_WAIT_SOURCE="${DEVICE_WAIT_SOURCE:-$REPO_ROOT/wait-for-modem-device.sh}"
HELPER_DIR="${HELPER_DIR:-/usr/local/lib/sms-to-telegram}"
CHECK_SERVICE_SOURCE="${CHECK_SERVICE_SOURCE:-$REPO_ROOT/sms-modem-check.service}"
CHECK_TIMER_SOURCE="${CHECK_TIMER_SOURCE:-$REPO_ROOT/sms-modem-check.timer}"
NOTIFY_SCRIPT_SOURCE="${NOTIFY_SCRIPT_SOURCE:-$REPO_ROOT/systemd-notify-on-failure.sh}"
NOTIFY_DIR="${NOTIFY_DIR:-/usr/local/lib/systemd-notify}"

IMAGE_INPUTS=(
  Dockerfile
  pyproject.toml
  uv.lock
  .python-version
  entrypoint.sh
  gammurc
  enqueue_sms.py
  send_worker.py
  check_forwarder_health.py
)

load_previous_fingerprint() {
  if [ -f "$STATE_FILE" ]; then
    python3 - <<'PY' "$STATE_FILE"
import json, sys
try:
    print(json.load(open(sys.argv[1])).get("source_fingerprint", ""))
except Exception:
    print("")
PY
  fi
}

load_previous_built_at() {
  if [ -f "$STATE_FILE" ]; then
    python3 - <<'PY' "$STATE_FILE"
import json, sys
try:
    print(json.load(open(sys.argv[1])).get("last_built_at", ""))
except Exception:
    print("")
PY
  fi
}

emit_git_version_state() {
  (
    cd "$REPO_ROOT"
    if ! git rev-parse --git-dir >/dev/null 2>&1; then
      printf '%s\0' "git-unavailable"
      return
    fi

    printf '%s\0' ".git/HEAD"
    git rev-parse HEAD

    printf '%s\0' ".git/describe"
    if git describe --dirty --tags --long --always --match 'v[0-9]*' >/dev/null 2>&1; then
      git describe --dirty --tags --long --always --match 'v[0-9]*'
    else
      git rev-parse --short HEAD
    fi
  )
}

compute_source_fingerprint() {
  (
    cd "$REPO_ROOT"
    {
      for path in "${IMAGE_INPUTS[@]}"; do
        printf '%s\0' "$path"
        cat "$path"
      done
      find sms_forwarder -type f | sort | while read -r path; do
        printf '%s\0' "$path"
        cat "$path"
      done
      emit_git_version_state
    } | python3 -c 'import hashlib, sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())'
  )
}

image_exists() {
  sudo -- podman image exists "$IMAGE_NAME"
}

inspect_image_id() {
  sudo -- podman image inspect "$IMAGE_NAME" --format '{{.Id}}' 2>/dev/null || echo ""
}

is_local_image() {
  [[ "$IMAGE_NAME" == localhost/* ]]
}

write_state_file() {
  local fingerprint="$1"
  local image_id="$2"
  local built_at="$3"
  local deployed_at="$4"
  mkdir -p "$STATE_DIR"
  python3 - <<'PY' "$STATE_FILE" "$IMAGE_NAME" "$image_id" "$fingerprint" "$built_at" "$deployed_at"
import json, sys
from pathlib import Path
state = {
    "image": sys.argv[2],
    "image_id": sys.argv[3],
    "source_fingerprint": sys.argv[4],
    "last_built_at": sys.argv[5],
    "last_deployed_at": sys.argv[6],
}
Path(sys.argv[1]).write_text(json.dumps(state, indent=2) + "\n")
PY
}

QUEUE_HOST_DIR="${QUEUE_HOST_DIR:-/var/lib/sms-to-telegram-queue}"

install_quadlet_unit() {
  sudo -- install -D -m 0644 "$QUADLET_SOURCE" "$QUADLET_TARGET"
}

create_host_dirs() {
  sudo -- mkdir -p "$QUEUE_HOST_DIR"
}

# The modem re-enumerates on its own and does not return on the same tty name.
# AddDevice resolves the by-id symlink only at container creation, so a restart
# is the only way to reattach; this rule triggers one the moment it reappears.
install_modem_reattach() {
  # The unit is installed verbatim, so its ExecStartPre helper must land at the
  # fixed path the unit names rather than in the checkout.
  sudo -- install -D -m 0755 "$DEVICE_WAIT_SOURCE" "$HELPER_DIR/wait-for-modem-device.sh"
  sudo -- install -D -m 0644 "$REATTACH_UNIT_SOURCE" "$SYSTEMD_UNIT_DIR/sms-modem-reattach.service"
  sudo -- install -D -m 0644 "$UDEV_RULE_SOURCE" "$UDEV_RULE_DIR/99-sms-modem-reattach.rules"
  sudo -- udevadm control --reload-rules
}

# The OnFailure notifier is shared by every quadlet on this host, so it lived
# outside version control until a re-enumerating modem turned it into a source
# of 1000+ Telegram messages a day. Install it from here so the throttle is
# reproducible rather than a hand-edit on the host.
install_failure_notifier() {
  sudo -- install -D -m 0755 "$NOTIFY_SCRIPT_SOURCE" "$NOTIFY_DIR/on-failure.sh"
}

# The checker runs from the repo checkout; only the units are installed, with
# ExecStart rewritten to this checkout's path.
install_modem_check() {
  local rendered
  rendered="$(mktemp)"
  trap 'rm -f "$rendered"' EXIT
  sed "s|__REPO_ROOT__|$REPO_ROOT|g" "$CHECK_SERVICE_SOURCE" > "$rendered"
  sudo -- install -D -m 0644 "$rendered" "$SYSTEMD_UNIT_DIR/sms-modem-check.service"
  rm -f "$rendered"
  trap - EXIT
  sudo -- install -D -m 0644 "$CHECK_TIMER_SOURCE" "$SYSTEMD_UNIT_DIR/sms-modem-check.timer"
  sudo -- systemctl daemon-reload
  sudo -- systemctl enable --now sms-modem-check.timer
}

restart_service() {
  sudo -- systemctl daemon-reload
  sudo -- systemctl restart sms-to-telegram.service
}

if [ "${1:-}" = "--print-fingerprint" ]; then
  compute_source_fingerprint
  exit 0
fi

main() {
  local fingerprint previous_fingerprint previous_built_at reason built_at image_id deployed_at
  fingerprint="$(compute_source_fingerprint)"
  previous_fingerprint="$(load_previous_fingerprint)"
  previous_built_at="$(load_previous_built_at)"
  built_at=""

  if is_local_image; then
    if ! image_exists; then
      reason="image missing"
    elif [ "$fingerprint" != "$previous_fingerprint" ]; then
      reason="source fingerprint changed"
    else
      reason=""
    fi

    if [ -n "$reason" ]; then
      echo "build triggered: $reason"
      (
        cd "$REPO_ROOT"
        sudo -- podman build -t "$IMAGE_NAME" .
      )
      built_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    else
      echo "build skipped: fingerprint unchanged"
      built_at="$previous_built_at"
    fi
  else
    echo "pulling remote image $IMAGE_NAME"
    sudo -- podman pull "$IMAGE_NAME"
    built_at="$previous_built_at"
  fi

  image_id="$(inspect_image_id)"
  create_host_dirs
  install_quadlet_unit
  install_modem_reattach
  install_modem_check
  install_failure_notifier
  restart_service
  deployed_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  write_state_file "$fingerprint" "$image_id" "${built_at:-$deployed_at}" "$deployed_at"
  echo "deployed image: $image_id"
}

main "$@"
