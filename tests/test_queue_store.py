from datetime import datetime, timezone
import json
import os
from pathlib import Path

from sms_forwarder.queue_store import QueueStore


def build_payload() -> dict:
    now = datetime(2026, 4, 29, 12, 0, tzinfo=timezone.utc)
    return {
        "id": "msg-1",
        "received_at": now.isoformat(),
        "sender": "+491234",
        "text": "hello 'quoted'\nline two",
        "attempts": 0,
        "next_attempt_at": now.isoformat(),
        "last_error": None,
        "telegram_chat_id": "123456",
    }


def test_queue_store_creates_state_directories(tmp_path):
    store = QueueStore(tmp_path)

    assert store.root == tmp_path
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "failed",
        "pending",
        "processing",
        "sent",
    ]


def test_enqueue_writes_message_atomically(tmp_path, monkeypatch):
    store = QueueStore(tmp_path)
    payload = build_payload()
    replace_calls: list[tuple[Path, Path]] = []
    original_replace = Path.replace

    def replace_spy(source: Path, target: Path) -> Path:
        replace_calls.append((source, target))
        assert source.suffixes[-2:] == [".json", ".tmp"]
        assert source.exists()
        assert not target.exists()
        return original_replace(source, target)

    monkeypatch.setattr(Path, "replace", replace_spy)

    message_path = store.enqueue(payload)

    assert replace_calls == [(message_path.with_suffix(".json.tmp"), message_path)]
    assert message_path.parent.name == "pending"
    assert list((tmp_path / "pending").iterdir()) == [message_path]
    assert not list((tmp_path / "pending").glob("*.tmp"))
    assert json.loads(message_path.read_text()) == payload


def test_enqueue_syncs_file_and_directory_for_durability(tmp_path, monkeypatch):
    store = QueueStore(tmp_path)
    payload = build_payload()
    fsync_calls: list[int] = []
    replace_call_count = 0
    original_fsync = os.fsync
    original_replace = Path.replace

    def fsync_spy(fd: int) -> None:
        fsync_calls.append(fd)
        original_fsync(fd)

    def replace_spy(source: Path, target: Path) -> Path:
        nonlocal replace_call_count
        replace_call_count += 1
        assert fsync_calls, "temp file must be fsynced before rename"
        return original_replace(source, target)

    monkeypatch.setattr(os, "fsync", fsync_spy)
    monkeypatch.setattr(Path, "replace", replace_spy)

    store.enqueue(payload)

    assert replace_call_count == 1
    assert len(fsync_calls) >= 2


def test_recover_stale_processing_moves_items_back_to_pending(tmp_path):
    store = QueueStore(tmp_path)
    processing_path = tmp_path / "processing" / "orphan.json"
    processing_path.parent.mkdir(parents=True, exist_ok=True)
    processing_path.write_text('{"id":"orphan"}')

    recovered = store.recover_stale_processing()

    assert recovered == [tmp_path / "pending" / "orphan.json"]
    assert (tmp_path / "pending" / "orphan.json").exists()
    assert not processing_path.exists()
