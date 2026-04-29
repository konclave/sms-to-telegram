import json
from datetime import datetime, timezone

from sms_forwarder.enqueue_hook import enqueue_from_environment, main
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
    payloads = [json.loads(path.read_text()) for path in created]
    assert payloads[0]["sender"] == "+49111"
    assert payloads[0]["text"] == "first line\nsecond line"
    assert payloads[1]["sender"] == "+49222"
    assert payloads[1]["text"] == "I'm quoted"
    assert all(payload["telegram_chat_id"] == "987654" for payload in payloads)


def test_enqueue_from_environment_returns_empty_list_when_no_messages(tmp_path, monkeypatch):
    monkeypatch.setenv("SMS_MESSAGES", "0")

    created = enqueue_from_environment(QueueStore(tmp_path))

    assert created == []


def test_main_uses_queue_root_and_logs_each_created_path(tmp_path, monkeypatch):
    queue_root = tmp_path / "queue"
    log_path = tmp_path / "enqueue.log"
    monkeypatch.setenv("QUEUE_ROOT", str(queue_root))
    monkeypatch.setenv("ENQUEUE_LOG_PATH", str(log_path))
    monkeypatch.setenv("SMS_MESSAGES", "2")
    monkeypatch.setenv("SMS_1_NUMBER", "+49111")
    monkeypatch.setenv("SMS_1_TEXT", "first line")
    monkeypatch.setenv("SMS_2_NUMBER", "+49222")
    monkeypatch.setenv("SMS_2_TEXT", "second line")
    monkeypatch.setenv("CHAT_ID", "987654")

    result = main()

    assert result == 0
    pending_paths = sorted((queue_root / "pending").glob("*.json"))
    assert len(pending_paths) == 2
    assert sorted(log_path.read_text().splitlines()) == sorted([
        f"event=enqueue_success path={pending_paths[0]}",
        f"event=enqueue_success path={pending_paths[1]}",
    ])


def test_main_prints_to_stdout_when_log_path_is_unset(tmp_path, monkeypatch, capsys):
    queue_root = tmp_path / "queue"
    monkeypatch.setenv("QUEUE_ROOT", str(queue_root))
    monkeypatch.delenv("ENQUEUE_LOG_PATH", raising=False)
    monkeypatch.setenv("SMS_MESSAGES", "1")
    monkeypatch.setenv("SMS_1_NUMBER", "+49111")
    monkeypatch.setenv("SMS_1_TEXT", "first line")
    monkeypatch.setenv("CHAT_ID", "987654")

    result = main()

    assert result == 0
    captured = capsys.readouterr()
    pending_paths = list((queue_root / "pending").glob("*.json"))
    assert len(pending_paths) == 1
    assert captured.out.splitlines() == [f"event=enqueue_success path={pending_paths[0]}"]


def test_main_falls_back_to_stdout_when_log_path_cannot_be_opened(tmp_path, monkeypatch, capsys):
    queue_root = tmp_path / "queue"
    monkeypatch.setenv("QUEUE_ROOT", str(queue_root))
    monkeypatch.setenv("ENQUEUE_LOG_PATH", str(tmp_path / "missing" / "enqueue.log"))
    monkeypatch.setenv("SMS_MESSAGES", "1")
    monkeypatch.setenv("SMS_1_NUMBER", "+49111")
    monkeypatch.setenv("SMS_1_TEXT", "first line")
    monkeypatch.setenv("CHAT_ID", "987654")

    result = main()

    assert result == 0
    captured = capsys.readouterr()
    pending_paths = list((queue_root / "pending").glob("*.json"))
    assert len(pending_paths) == 1
    assert captured.out.splitlines() == [f"event=enqueue_success path={pending_paths[0]}"]
