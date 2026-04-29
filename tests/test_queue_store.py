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
    events: list[tuple[str, int | str]] = []
    directory_fd: int | None = None
    original_fsync = os.fsync
    original_open = os.open
    original_replace = Path.replace

    def fsync_spy(fd: int) -> None:
        events.append(("fsync", fd))
        original_fsync(fd)

    def open_spy(path, flags: int, mode: int = 0o777) -> int:
        nonlocal directory_fd
        fd = original_open(path, flags, mode)
        if Path(path) == tmp_path / "pending":
            directory_fd = fd
            events.append(("open_dir", fd))
        return fd

    def replace_spy(source: Path, target: Path) -> Path:
        events.append(("replace", str(target)))
        assert any(event[0] == "fsync" for event in events), "temp file must be fsynced before rename"
        return original_replace(source, target)

    monkeypatch.setattr(os, "fsync", fsync_spy)
    monkeypatch.setattr(os, "open", open_spy)
    monkeypatch.setattr(Path, "replace", replace_spy)

    store.enqueue(payload)

    expected_target = str(next((tmp_path / "pending").glob("*.json")))
    assert directory_fd is not None
    replace_index = events.index(("replace", expected_target))
    open_dir_index = events.index(("open_dir", directory_fd))
    assert ("fsync", directory_fd) in events
    assert open_dir_index > replace_index
    assert events[open_dir_index + 1] == ("fsync", directory_fd)


def test_recover_stale_processing_moves_items_back_to_pending(tmp_path):
    store = QueueStore(tmp_path)
    processing_path = tmp_path / "processing" / "orphan.json"
    processing_path.parent.mkdir(parents=True, exist_ok=True)
    processing_path.write_text('{"id":"orphan"}')

    recovered = store.recover_stale_processing()

    assert recovered == [tmp_path / "pending" / "orphan.json"]
    assert (tmp_path / "pending" / "orphan.json").exists()
    assert not processing_path.exists()
