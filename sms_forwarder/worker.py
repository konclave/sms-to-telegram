from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import time

from sms_forwarder.queue_store import QueueStore
from sms_forwarder.telegram_api import (
    RetryableDeliveryError,
    TelegramClient,
    TerminalDeliveryError,
)


RETRY_DELAYS = (30, 120, 600, 1800, 3600)


class DeliveryWorker:
    def __init__(
        self,
        store: QueueStore,
        send_message,
        now=None,
        max_attempts: int = 24,
    ):
        self.store = store
        self.send_message = send_message
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.max_attempts = max_attempts
        self.last_event: str | None = None
        self.last_payload: dict | None = None

    def process_next_due_message(self) -> bool:
        self.last_event = None
        self.last_payload = None
        now = self.now()
        pending_path, payload = self._claim_next_due_message(now)
        if pending_path is None or payload is None:
            return False

        processing_path = self.store.root / "processing" / pending_path.name
        pending_path.replace(processing_path)
        try:
            self.send_message(payload)
        except RetryableDeliveryError as exc:
            payload["attempts"] = payload.get("attempts", 0) + 1
            payload["last_error"] = str(exc)
            if payload["attempts"] >= self.max_attempts:
                self._move_payload(processing_path, "failed", payload)
                self.last_event = "delivery_failed"
            else:
                payload["next_attempt_at"] = self.next_attempt_at(payload["attempts"]).isoformat()
                self._move_payload(processing_path, "pending", payload)
                self.last_event = "delivery_retry"
            self.last_payload = payload
            return True
        except TerminalDeliveryError as exc:
            payload["last_error"] = str(exc)
            self._move_payload(processing_path, "failed", payload)
            self.last_event = "delivery_failed"
            self.last_payload = payload
            return True

        self._move_payload(processing_path, "sent", payload)
        self.last_event = "delivery_success"
        self.last_payload = payload
        return True

    def next_attempt_at(self, attempts: int) -> datetime:
        index = min(max(attempts - 1, 0), len(RETRY_DELAYS) - 1)
        return self.now() + timedelta(seconds=RETRY_DELAYS[index])

    def prune_history(self, state: str, keep_latest: int) -> None:
        directory = self.store.root / state
        paths = sorted(directory.glob("*.json"))
        excess = len(paths) - keep_latest
        if excess <= 0:
            return
        for path in paths[:excess]:
            path.unlink()

    def _claim_next_due_message(self, now: datetime) -> tuple[Path | None, dict | None]:
        for path in sorted((self.store.root / "pending").glob("*.json")):
            payload = json.loads(path.read_text())
            due_at = datetime.fromisoformat(payload["next_attempt_at"])
            if due_at <= now:
                return path, payload
        return None, None

    def _move_payload(self, source: Path, state: str, payload: dict) -> Path:
        destination = self.store.root / state / source.name
        temp_path = destination.with_suffix(".json.tmp")
        with temp_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        temp_path.replace(destination)
        if source.exists():
            source.unlink()
        return destination


def main() -> int:
    queue_root = os.environ.get("QUEUE_ROOT", "/var/spool/sms-forwarder")
    idle_sleep_seconds = float(os.environ.get("WORKER_IDLE_SLEEP_SECONDS", "5"))
    keep_sent = int(os.environ.get("KEEP_SENT", "1000"))
    keep_failed = int(os.environ.get("KEEP_FAILED", "1000"))
    max_attempts = int(os.environ.get("MAX_ATTEMPTS", "24"))

    store = QueueStore(queue_root)
    client = TelegramClient(bot_token=os.environ["BOT_TOKEN"])
    worker = DeliveryWorker(
        store=store,
        send_message=client.send_message,
        max_attempts=max_attempts,
    )

    print(f"event=worker_startup queue_root={queue_root} max_attempts={max_attempts}")
    recovered = store.recover_stale_processing()
    print(f"event=stale_recovered count={len(recovered)}")

    while True:
        processed = worker.process_next_due_message()
        worker.prune_history("sent", keep_sent)
        worker.prune_history("failed", keep_failed)

        if processed:
            payload = worker.last_payload or {}
            if worker.last_event == "delivery_retry":
                print(
                    "event=delivery_retry "
                    f"id={payload.get('id')} attempts={payload.get('attempts')} "
                    f"next_attempt_at={payload.get('next_attempt_at')}"
                )
            elif worker.last_event == "delivery_failed":
                print(
                    "event=delivery_failed "
                    f"id={payload.get('id')} attempts={payload.get('attempts')} "
                    f"error={payload.get('last_error')}"
                )
            elif worker.last_event == "delivery_success":
                print(f"event=delivery_success id={payload.get('id')}")
            continue

        print(f"event=worker_idle sleep_seconds={idle_sleep_seconds}")
        time.sleep(idle_sleep_seconds)


__all__ = [
    "DeliveryWorker",
    "RetryableDeliveryError",
    "TerminalDeliveryError",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
