from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import os
import uuid

from sms_forwarder.queue_store import QueueStore


def enqueue_from_environment(store: QueueStore, now: datetime | None = None) -> list[Path]:
    now = now or datetime.now(timezone.utc)
    message_count = int(os.environ.get("SMS_MESSAGES", "0"))
    created = []

    for index in range(1, message_count + 1):
        message_time = now + timedelta(microseconds=index - 1)
        sender = os.environ.get(f"SMS_{index}_NUMBER", "")
        text = os.environ.get(f"SMS_{index}_TEXT", "")
        payload = {
            "id": uuid.uuid4().hex,
            "received_at": message_time.isoformat(),
            "sender": sender,
            "text": text,
            "attempts": 0,
            "next_attempt_at": message_time.isoformat(),
            "last_error": None,
            "telegram_chat_id": os.environ["CHAT_ID"],
        }
        created.append(store.enqueue(payload))

    return created


def main() -> int:
    queue_root = os.environ.get("QUEUE_ROOT", "/var/spool/sms-forwarder")
    created = enqueue_from_environment(QueueStore(queue_root))
    for path in created:
        print(f"event=enqueue_success path={path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
