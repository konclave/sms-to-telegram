import json
from datetime import datetime, timezone

from sms_forwarder.enqueue_hook import enqueue_from_environment
from sms_forwarder.queue_store import QueueStore


def test_enqueue_from_environment_creates_one_file_per_sms(tmp_path, monkeypatch):
    monkeypatch.setenv("SMS_MESSAGES", "2")
    monkeypatch.setenv("SMS_1_NUMBER", "+49111")
    monkeypatch.setenv("SMS_1_TEXT", "first line\nsecond line")
    monkeypatch.setenv("SMS_2_NUMBER", "+49222")
    monkeypatch.setenv("SMS_2_TEXT", "I'm quoted")
    monkeypatch.setenv("CHAT_ID", "987654")

    created = enqueue_from_environment(
        QueueStore(tmp_path),
        now=datetime(2026, 4, 29, 12, 30, tzinfo=timezone.utc),
    )

    assert len(created) == 2
    payloads = [json.loads(path.read_text()) for path in sorted(created)]
    assert payloads[0]["sender"] == "+49111"
    assert payloads[0]["text"] == "first line\nsecond line"
    assert payloads[1]["sender"] == "+49222"
    assert payloads[1]["text"] == "I'm quoted"
    assert all(payload["telegram_chat_id"] == "987654" for payload in payloads)


def test_enqueue_from_environment_returns_empty_list_when_no_messages(tmp_path, monkeypatch):
    monkeypatch.setenv("SMS_MESSAGES", "0")

    created = enqueue_from_environment(QueueStore(tmp_path))

    assert created == []
