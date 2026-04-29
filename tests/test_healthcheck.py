from datetime import datetime, timezone

from sms_forwarder.healthcheck import healthcheck
from sms_forwarder.queue_store import QueueStore


def test_healthcheck_fails_when_worker_pid_is_missing(tmp_path):
    store = QueueStore(tmp_path)

    status, detail = healthcheck(
        store.root,
        tmp_path / "worker.pid",
        max_oldest_due_seconds=60,
        now=datetime(2026, 4, 29, 14, 0, tzinfo=timezone.utc),
    )

    assert status == 1
    assert detail == "worker pid file missing"


def test_healthcheck_fails_when_due_message_is_too_old(tmp_path):
    store = QueueStore(tmp_path)
    old_due = datetime(2026, 4, 29, 13, 55, tzinfo=timezone.utc)
    store.enqueue(
        {
            "id": "stale",
            "received_at": old_due.isoformat(),
            "sender": "+49123",
            "text": "hello",
            "attempts": 0,
            "next_attempt_at": old_due.isoformat(),
            "last_error": None,
            "telegram_chat_id": "1",
        }
    )
    worker_pid = tmp_path / "worker.pid"
    worker_pid.write_text(str(__import__("os").getpid()))

    status, detail = healthcheck(
        store.root,
        worker_pid,
        max_oldest_due_seconds=60,
        now=datetime(2026, 4, 29, 14, 0, tzinfo=timezone.utc),
    )

    assert status == 1
    assert detail.startswith("oldest due message age exceeded")
