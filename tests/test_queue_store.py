from datetime import datetime, timezone
import json

from sms_forwarder.queue_store import QueueStore


def test_enqueue_writes_message_atomically(tmp_path):
    store = QueueStore(tmp_path)
    now = datetime(2026, 4, 29, 12, 0, tzinfo=timezone.utc)

    payload = {
        "id": "msg-1",
        "received_at": now.isoformat(),
        "sender": "+491234",
        "text": "hello 'quoted'\nline two",
        "attempts": 0,
        "next_attempt_at": now.isoformat(),
        "last_error": None,
        "telegram_chat_id": "123456",
    }

    message_path = store.enqueue(payload)

    assert message_path.parent.name == "pending"
    assert list((tmp_path / "pending").iterdir()) == [message_path]
    assert not list((tmp_path / "pending").glob("*.tmp"))
    assert json.loads(message_path.read_text()) == payload


def test_recover_stale_processing_moves_items_back_to_pending(tmp_path):
    store = QueueStore(tmp_path)
    processing_path = tmp_path / "processing" / "orphan.json"
    processing_path.parent.mkdir(parents=True, exist_ok=True)
    processing_path.write_text('{"id":"orphan"}')

    recovered = store.recover_stale_processing()

    assert recovered == [tmp_path / "pending" / "orphan.json"]
    assert (tmp_path / "pending" / "orphan.json").exists()
    assert not processing_path.exists()
