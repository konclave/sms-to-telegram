from datetime import datetime, timedelta, timezone
import json

from sms_forwarder.queue_store import QueueStore
from sms_forwarder.worker import DeliveryWorker, RetryableDeliveryError, TerminalDeliveryError


def build_payload(now):
    return {
        "id": "message-1",
        "received_at": now.isoformat(),
        "sender": "+49123",
        "text": "hello",
        "attempts": 0,
        "next_attempt_at": now.isoformat(),
        "last_error": None,
        "telegram_chat_id": "123",
    }


def test_process_due_message_moves_successes_to_sent(tmp_path):
    now = datetime(2026, 4, 29, 13, 0, tzinfo=timezone.utc)
    store = QueueStore(tmp_path)
    store.enqueue(build_payload(now))
    calls = []

    worker = DeliveryWorker(
        store=store,
        send_message=lambda payload: calls.append(payload["text"]),
        now=lambda: now,
    )

    processed = worker.process_next_due_message()

    assert processed is True
    assert calls == ["hello"]
    assert len(list((tmp_path / "sent").glob("*.json"))) == 1
    assert not list((tmp_path / "pending").glob("*.json"))


def test_process_due_message_requeues_retryable_errors(tmp_path):
    now = datetime(2026, 4, 29, 13, 0, tzinfo=timezone.utc)
    store = QueueStore(tmp_path)
    store.enqueue(build_payload(now))

    worker = DeliveryWorker(
        store=store,
        send_message=lambda payload: (_ for _ in ()).throw(RetryableDeliveryError("timeout")),
        now=lambda: now,
    )

    processed = worker.process_next_due_message()

    assert processed is True
    pending_files = list((tmp_path / "pending").glob("*.json"))
    assert len(pending_files) == 1
    payload = json.loads(pending_files[0].read_text())
    assert payload["attempts"] == 1
    assert payload["last_error"] == "timeout"
    assert payload["next_attempt_at"] == (now + timedelta(seconds=30)).isoformat()


def test_process_due_message_moves_terminal_errors_to_failed(tmp_path):
    now = datetime(2026, 4, 29, 13, 0, tzinfo=timezone.utc)
    store = QueueStore(tmp_path)
    store.enqueue(build_payload(now))

    worker = DeliveryWorker(
        store=store,
        send_message=lambda payload: (_ for _ in ()).throw(TerminalDeliveryError("bad token")),
        now=lambda: now,
    )

    processed = worker.process_next_due_message()

    assert processed is True
    failed_files = list((tmp_path / "failed").glob("*.json"))
    assert len(failed_files) == 1
    payload = json.loads(failed_files[0].read_text())
    assert payload["last_error"] == "bad token"


def test_process_due_message_stops_retrying_after_max_attempts(tmp_path):
    now = datetime(2026, 4, 29, 13, 0, tzinfo=timezone.utc)
    store = QueueStore(tmp_path)
    payload = build_payload(now)
    payload["attempts"] = 4
    store.enqueue(payload)

    worker = DeliveryWorker(
        store=store,
        send_message=lambda payload: (_ for _ in ()).throw(RetryableDeliveryError("timeout")),
        now=lambda: now,
        max_attempts=5,
    )

    processed = worker.process_next_due_message()

    assert processed is True
    failed_files = list((tmp_path / "failed").glob("*.json"))
    assert len(failed_files) == 1
    payload = json.loads(failed_files[0].read_text())
    assert payload["attempts"] == 5
    assert payload["last_error"] == "timeout"


def test_prune_history_keeps_only_the_newest_files(tmp_path):
    now = datetime(2026, 4, 29, 13, 0, tzinfo=timezone.utc)
    store = QueueStore(tmp_path)
    worker = DeliveryWorker(store=store, send_message=lambda payload: None, now=lambda: now)

    sent_dir = tmp_path / "sent"
    sent_dir.mkdir(exist_ok=True)
    for name in ["one.json", "two.json", "three.json"]:
        path = sent_dir / name
        path.write_text("{}")

    worker.prune_history("sent", keep_latest=2)

    assert sorted(path.name for path in sent_dir.glob("*.json")) == ["three.json", "two.json"]
