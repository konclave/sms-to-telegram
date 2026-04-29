from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from contextlib import nullcontext
import os
import uuid

from sms_forwarder.queue_store import QueueStore


def enqueue_from_environment(store: QueueStore, now: datetime | None = None) -> list[Path]:
    now = now or datetime.now(timezone.utc)
    message_count = int(os.environ.get("SMS_MESSAGES", "0"))
    created = []

    for index in range(1, message_count + 1):
        sender = os.environ.get(f"SMS_{index}_NUMBER", "")
        text = os.environ.get(f"SMS_{index}_TEXT", "")
        payload = {
            "id": uuid.uuid4().hex,
            "received_at": now.isoformat(),
            "sender": sender,
            "text": text,
            "attempts": 0,
            "next_attempt_at": now.isoformat(),
            "last_error": None,
            "telegram_chat_id": os.environ["CHAT_ID"],
        }
        created.append(store.enqueue(payload))

    return created


def _log_destination(log_path: str | None):
    if not log_path:
        return nullcontext(None)
    try:
        return open(log_path, "a", encoding="utf-8")
    except OSError:
        return nullcontext(None)


def main() -> int:
    queue_root = os.environ.get("QUEUE_ROOT", "/var/spool/sms-forwarder")
    log_path = os.environ.get("ENQUEUE_LOG_PATH")
    created = enqueue_from_environment(QueueStore(queue_root))
    with _log_destination(log_path) as stream:
        for path in created:
            message = f"event=enqueue_success path={path}"
            if log_path is None:
                print(message)
            elif stream is not None:
                print(message, file=stream)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
