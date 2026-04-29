from __future__ import annotations

import json
import os
from pathlib import Path
import uuid


STATE_DIRS = ("pending", "processing", "sent", "failed")


class QueueStore:
    def __init__(self, root: Path | str):
        self.root = Path(root)
        for state in STATE_DIRS:
            (self.root / state).mkdir(parents=True, exist_ok=True)

    def enqueue(self, payload: dict) -> Path:
        file_name = f"{payload['received_at'].replace(':', '-')}-{uuid.uuid4().hex}.json"
        pending_path = self.root / "pending" / file_name
        temp_path = pending_path.with_suffix(".json.tmp")
        with temp_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        temp_path.replace(pending_path)
        directory_fd = os.open(pending_path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return pending_path

    def recover_stale_processing(self) -> list[Path]:
        recovered = []
        for processing_path in sorted((self.root / "processing").glob("*.json")):
            pending_path = self.root / "pending" / processing_path.name
            processing_path.replace(pending_path)
            recovered.append(pending_path)
        return recovered
