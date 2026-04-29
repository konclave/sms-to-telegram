from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path


def worker_alive(pid_file: Path) -> bool:
    if not pid_file.exists():
        return False
    try:
        pid = int(pid_file.read_text().strip())
    except ValueError:
        return False

    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def healthcheck(
    queue_root: Path | str,
    pid_file: Path | str,
    max_oldest_due_seconds: int,
    now: datetime | None = None,
) -> tuple[int, str]:
    queue_root = Path(queue_root)
    pid_file = Path(pid_file)
    now = now or datetime.now(timezone.utc)

    if not pid_file.exists():
        return 1, "worker pid file missing"
    if not worker_alive(pid_file):
        return 1, "worker process not running"

    for pending_path in sorted((queue_root / "pending").glob("*.json")):
        payload = json.loads(pending_path.read_text())
        due_at = datetime.fromisoformat(payload["next_attempt_at"])
        age_seconds = int((now - due_at).total_seconds())
        if age_seconds > max_oldest_due_seconds:
            return 1, f"oldest due message age exceeded: {age_seconds}s"

    return 0, "ok"


def main() -> int:
    status, detail = healthcheck(
        os.environ.get("QUEUE_ROOT", "/var/spool/sms-forwarder"),
        os.environ.get("WORKER_PID_FILE", "/var/run/sms-forwarder-worker.pid"),
        int(os.environ.get("QUEUE_HEALTH_MAX_AGE_SECONDS", "300")),
    )
    print(detail)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
