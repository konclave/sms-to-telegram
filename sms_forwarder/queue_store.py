from __future__ import annotations

from pathlib import Path
import json
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
        temp_path.write_text(json.dumps(payload, ensure_ascii=False))
        temp_path.replace(pending_path)
        return pending_path

    def recover_stale_processing(self) -> list[Path]:
        recovered = []
        for processing_path in sorted((self.root / "processing").glob("*.json")):
            pending_path = self.root / "pending" / processing_path.name
            processing_path.replace(pending_path)
            recovered.append(pending_path)
        return recovered
