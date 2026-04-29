#!/bin/bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
STATE_DIR="${STATE_DIR:-$REPO_ROOT/.deploy}"
STATE_FILE="$STATE_DIR/sms-to-telegram-state.json"
IMAGE_NAME="${IMAGE_NAME:-localhost/sms-to-telegram:latest}"
QUADLET_SOURCE="${QUADLET_SOURCE:-$REPO_ROOT/sms-to-telegram.container}"
QUADLET_DIR="${QUADLET_DIR:-/etc/containers/systemd}"
QUADLET_TARGET="$QUADLET_DIR/sms-to-telegram.container"

IMAGE_INPUTS=(
  Dockerfile
  Dockerfile.alpine
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
    } | python3 -c 'import hashlib, sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())'
  )
}

image_exists() {
  podman image exists "$IMAGE_NAME"
}

inspect_image_id() {
  podman image inspect "$IMAGE_NAME" --format '{{.Id}}'
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

install_quadlet_unit() {
  sudo -- install -D -m 0644 "$QUADLET_SOURCE" "$QUADLET_TARGET"
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
      podman build -t "$IMAGE_NAME" .
    )
    built_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  else
    echo "build skipped: fingerprint unchanged"
    built_at="$previous_built_at"
  fi

  image_id="$(inspect_image_id)"
  install_quadlet_unit
  restart_service
  deployed_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  write_state_file "$fingerprint" "$image_id" "${built_at:-$deployed_at}" "$deployed_at"
  echo "deployed image: $image_id"
}

main "$@"
