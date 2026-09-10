# SMS Forwarder Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the fragile shell-based Telegram sender with a file-backed Python queue and worker so inbound SMS receipt is decoupled from Telegram delivery and survives transient failures.

**Architecture:** `gammu-smsd` keeps ownership of SMS ingress and calls a Python enqueue hook. The hook writes one JSON message file per SMS into a spool directory, and a separate Python worker process drains that queue with retries, backoff, and structured logs. The existing container entrypoint remains the integration point, but it now boots the worker, restores stale queue items, and fails the container if the worker dies.

**Tech Stack:** Python 3 standard library, `pytest`, shell entrypoint, `gammu-smsd`, Docker/Podman

---

## File Structure

- Create: `requirements-dev.txt` for local test dependencies
- Create: `pytest.ini` for consistent test discovery
- Create: `sms_forwarder/__init__.py` for package discovery
- Create: `sms_forwarder/queue_store.py` for queue directory management and atomic file moves
- Create: `sms_forwarder/enqueue_hook.py` for `RunOnReceive` message ingestion
- Create: `sms_forwarder/telegram_api.py` for Telegram HTTP delivery and error classification
- Create: `sms_forwarder/worker.py` for retry scheduling, queue draining, and stale recovery
- Create: `sms_forwarder/healthcheck.py` for worker/queue health checks
- Create: `enqueue_sms.py` as the executable runtime wrapper for the enqueue hook
- Create: `send_worker.py` as the executable runtime wrapper for the worker
- Create: `check_forwarder_health.py` as the executable runtime wrapper for health checks
- Create: `tests/test_queue_store.py` for queue persistence behavior
- Create: `tests/test_enqueue_hook.py` for hook behavior with `gammu-smsd` environment variables
- Create: `tests/test_worker.py` for success, retry, and terminal failure handling
- Create: `tests/test_healthcheck.py` for unhealthy worker and stale backlog detection
- Modify: `gammurc` to replace the shell hook with the Python enqueue hook
- Modify: `entrypoint.sh` to create queue directories, recover stale work, start the worker, and supervise both processes
- Modify: `Dockerfile` to install Python and copy the package
- Modify: `Dockerfile.alpine` to install Python and copy the package
- Modify: `README.md` to document queue persistence, logs, and troubleshooting
- Modify: `sms-to-telegram.container` to document or provide persistent queue storage
- Modify: `sms-to-telegram.live.container` to document or provide persistent queue storage
- Delete: `sms_to_telegram.sh` once Python ingestion is wired in

### Task 1: Add Queue Storage With Failing Tests

**Files:**
- Create: `requirements-dev.txt`
- Create: `pytest.ini`
- Create: `sms_forwarder/__init__.py`
- Create: `sms_forwarder/queue_store.py`
- Test: `tests/test_queue_store.py`

- [ ] **Step 1: Write the failing tests for queue persistence and stale recovery**

Also create:

```text
# requirements-dev.txt
pytest==8.4.1
```

And:

```ini
# pytest.ini
[pytest]
testpaths = tests
pythonpath = .
```

```python
from datetime import datetime, timedelta, timezone
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
```

- [ ] **Step 2: Run the queue tests to verify they fail for the right reason**

Run: `python3 -m venv .venv && ./.venv/bin/pip install -r requirements-dev.txt && ./.venv/bin/python -m pytest tests/test_queue_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sms_forwarder'`

- [ ] **Step 3: Write the minimal queue storage implementation**

```python
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
```

Also add:

```python
# sms_forwarder/__init__.py
__all__ = ["queue_store"]
```

- [ ] **Step 4: Run the queue tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/test_queue_store.py -v`
Expected: PASS for both queue store tests

- [ ] **Step 5: Commit the queue storage baseline**

```bash
git add requirements-dev.txt pytest.ini sms_forwarder/__init__.py sms_forwarder/queue_store.py tests/test_queue_store.py
git commit -m "test: add queue storage foundation"
```

### Task 2: Add The Python Enqueue Hook With Failing Tests

**Files:**
- Create: `sms_forwarder/enqueue_hook.py`
- Test: `tests/test_enqueue_hook.py`

- [ ] **Step 1: Write the failing tests for ingesting `gammu-smsd` environment variables**

```python
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

    created = enqueue_from_environment(QueueStore(tmp_path), now=datetime(2026, 4, 29, 12, 30, tzinfo=timezone.utc))

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
```

- [ ] **Step 2: Run the enqueue hook tests to verify they fail**

Run: `./.venv/bin/python -m pytest tests/test_enqueue_hook.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sms_forwarder.enqueue_hook'`

- [ ] **Step 3: Write the minimal enqueue hook implementation**

```python
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import os
import uuid

from sms_forwarder.queue_store import QueueStore


def enqueue_from_environment(store: QueueStore, now: datetime | None = None) -> list[Path]:
    now = now or datetime.now(timezone.utc)
    message_count = int(os.environ.get("SMS_MESSAGES", "0"))
    created = []

    for index in range(1, message_count + 1):
        sender = os.environ.get(f"SMS_{index}_NUMBER", "")
        text = os.environ.get(f"SMS_{index}_TEXT", "")
        payload = {
            "id": uuid.uuid4().hex,
            "received_at": now.isoformat(),
            "sender": sender,
            "text": text,
            "attempts": 0,
            "next_attempt_at": now.isoformat(),
            "last_error": None,
            "telegram_chat_id": os.environ["CHAT_ID"],
        }
        created.append(store.enqueue(payload))

    return created


def main() -> int:
    queue_root = os.environ.get("QUEUE_ROOT", "/var/spool/sms-forwarder")
    created = enqueue_from_environment(QueueStore(queue_root))
    for path in created:
        print(f"event=enqueue_success path={path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the enqueue hook tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/test_enqueue_hook.py -v`
Expected: PASS for both enqueue hook tests

- [ ] **Step 5: Commit the enqueue hook**

```bash
git add sms_forwarder/enqueue_hook.py tests/test_enqueue_hook.py
git commit -m "feat: add SMS enqueue hook"
```

### Task 3: Add Worker Retry And Telegram Delivery With Failing Tests

**Files:**
- Create: `sms_forwarder/telegram_api.py`
- Create: `sms_forwarder/worker.py`
- Test: `tests/test_worker.py`

- [ ] **Step 1: Write the failing tests for success, retryable failure, and terminal failure**

```python
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
```

- [ ] **Step 2: Run the worker tests to verify they fail**

Run: `./.venv/bin/python -m pytest tests/test_worker.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sms_forwarder.worker'`

- [ ] **Step 3: Write the minimal Telegram client and worker implementation**

```python
# sms_forwarder/telegram_api.py
from __future__ import annotations

import json
from urllib import error, request


class RetryableDeliveryError(Exception):
    pass


class TerminalDeliveryError(Exception):
    pass


class TelegramClient:
    def __init__(self, bot_token: str, timeout_seconds: int = 10):
        self.url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        self.timeout_seconds = timeout_seconds

    def send_message(self, payload: dict) -> None:
        body = json.dumps(
            {"chat_id": payload["telegram_chat_id"], "text": f'{payload["sender"]}:\n{payload["text"]}'},
            ensure_ascii=False,
        ).encode("utf-8")
        req = request.Request(self.url, data=body, headers={"Content-Type": "application/json"}, method="POST")

        try:
            with request.urlopen(req, timeout=self.timeout_seconds) as response:
                response_body = response.read().decode("utf-8")
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            if exc.code == 429 or 500 <= exc.code < 600:
                raise RetryableDeliveryError(detail) from exc
            raise TerminalDeliveryError(detail) from exc
        except error.URLError as exc:
            raise RetryableDeliveryError(str(exc.reason)) from exc

        response_json = json.loads(response_body)
        if response_json.get("ok") is True:
            return
        description = response_json.get("description", "telegram request failed")
        if "chat not found" in description.lower() or "unauthorized" in description.lower():
            raise TerminalDeliveryError(description)
        raise RetryableDeliveryError(description)
```

```python
# sms_forwarder/worker.py
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import os
import time

from sms_forwarder.queue_store import QueueStore
from sms_forwarder.telegram_api import TelegramClient, RetryableDeliveryError, TerminalDeliveryError


class DeliveryWorker:
    def __init__(self, store: QueueStore, send_message, now=None, max_attempts: int = 24):
        self.store = store
        self.send_message = send_message
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.max_attempts = max_attempts

    def process_next_due_message(self) -> bool:
        for pending_path in sorted((self.store.root / "pending").glob("*.json")):
            payload = json.loads(pending_path.read_text())
            if datetime.fromisoformat(payload["next_attempt_at"]) > self.now():
                continue

            processing_path = self.store.root / "processing" / pending_path.name
            pending_path.replace(processing_path)

            try:
                self.send_message(payload)
            except RetryableDeliveryError as exc:
                payload["attempts"] += 1
                payload["last_error"] = str(exc)
                if payload["attempts"] >= self.max_attempts:
                    processing_path.write_text(json.dumps(payload, ensure_ascii=False))
                    processing_path.replace(self.store.root / "failed" / processing_path.name)
                    print(f"event=delivery_failed id={payload['id']} reason=max_attempts")
                    return True
                payload["next_attempt_at"] = self.next_attempt_at(payload["attempts"]).isoformat()
                processing_path.write_text(json.dumps(payload, ensure_ascii=False))
                processing_path.replace(self.store.root / "pending" / processing_path.name)
                print(f"event=delivery_retry id={payload['id']} attempts={payload['attempts']}")
                return True
            except TerminalDeliveryError as exc:
                payload["last_error"] = str(exc)
                processing_path.write_text(json.dumps(payload, ensure_ascii=False))
                processing_path.replace(self.store.root / "failed" / processing_path.name)
                print(f"event=delivery_failed id={payload['id']}")
                return True

            processing_path.replace(self.store.root / "sent" / processing_path.name)
            print(f"event=delivery_success id={payload['id']}")
            return True

        return False

    def next_attempt_at(self, attempts: int) -> datetime:
        delays = [30, 120, 600, 1800]
        seconds = delays[min(attempts - 1, len(delays) - 1)]
        if attempts > len(delays):
            seconds = 3600
        return self.now() + timedelta(seconds=seconds)

    def prune_history(self, state: str, keep_latest: int) -> None:
        files = sorted((self.store.root / state).glob("*.json"))
        while len(files) > keep_latest:
            files.pop(0).unlink()


def main() -> int:
    store = QueueStore(os.environ.get("QUEUE_ROOT", "/var/spool/sms-forwarder"))
    client = TelegramClient(os.environ["BOT_TOKEN"])
    worker = DeliveryWorker(
        store=store,
        send_message=client.send_message,
        max_attempts=int(os.environ.get("MAX_ATTEMPTS", "24")),
    )
    recovered = store.recover_stale_processing()
    print("event=worker_startup")
    if recovered:
        print(f"event=stale_recovered count={len(recovered)}")

    while True:
        processed = worker.process_next_due_message()
        worker.prune_history("sent", int(os.environ.get("SENT_RETENTION_COUNT", "500")))
        worker.prune_history("failed", int(os.environ.get("FAILED_RETENTION_COUNT", "500")))
        if not processed:
            time.sleep(5)
```

- [ ] **Step 4: Run the worker tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/test_worker.py -v`
Expected: PASS for success, retry, and terminal failure tests

- [ ] **Step 5: Commit the worker implementation**

```bash
git add sms_forwarder/telegram_api.py sms_forwarder/worker.py tests/test_worker.py
git commit -m "feat: add Telegram delivery worker"
```

### Task 4: Add Health Checks With Failing Tests

**Files:**
- Create: `sms_forwarder/healthcheck.py`
- Test: `tests/test_healthcheck.py`

- [ ] **Step 1: Write the failing tests for missing worker and stale due backlog**

```python
from datetime import datetime, timedelta, timezone
import json

from sms_forwarder.healthcheck import healthcheck
from sms_forwarder.queue_store import QueueStore


def test_healthcheck_fails_when_worker_pid_is_missing(tmp_path):
    store = QueueStore(tmp_path)
    status, detail = healthcheck(store.root, tmp_path / "worker.pid", max_oldest_due_seconds=60, now=datetime(2026, 4, 29, 14, 0, tzinfo=timezone.utc))

    assert status == 1
    assert detail == "worker pid file missing"


def test_healthcheck_fails_when_due_message_is_too_old(tmp_path):
    store = QueueStore(tmp_path)
    old_due = datetime(2026, 4, 29, 13, 55, tzinfo=timezone.utc)
    message_path = store.enqueue(
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

    status, detail = healthcheck(store.root, worker_pid, max_oldest_due_seconds=60, now=datetime(2026, 4, 29, 14, 0, tzinfo=timezone.utc))

    assert status == 1
    assert detail.startswith("oldest due message age exceeded")
```

- [ ] **Step 2: Run the health tests to verify they fail**

Run: `./.venv/bin/python -m pytest tests/test_healthcheck.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sms_forwarder.healthcheck'`

- [ ] **Step 3: Write the minimal healthcheck implementation**

```python
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import os
import signal


def worker_alive(pid_file: Path) -> bool:
    if not pid_file.exists():
        return False
    pid = int(pid_file.read_text().strip())
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def healthcheck(queue_root: Path | str, pid_file: Path | str, max_oldest_due_seconds: int, now: datetime | None = None) -> tuple[int, str]:
    queue_root = Path(queue_root)
    pid_file = Path(pid_file)
    now = now or datetime.now(timezone.utc)

    if not pid_file.exists():
        return 1, "worker pid file missing"
    if not worker_alive(pid_file):
        return 1, "worker process not running"

    for pending_path in sorted((queue_root / "pending").glob("*.json")):
        payload = json.loads(pending_path.read_text())
        due_at = datetime.fromisoformat(payload["next_attempt_at"])
        age_seconds = int((now - due_at).total_seconds())
        if age_seconds > max_oldest_due_seconds:
            return 1, f"oldest due message age exceeded: {age_seconds}s"

    return 0, "ok"


def main() -> int:
    status, detail = healthcheck(
        os.environ.get("QUEUE_ROOT", "/var/spool/sms-forwarder"),
        os.environ.get("WORKER_PID_FILE", "/var/run/sms-forwarder-worker.pid"),
        int(os.environ.get("QUEUE_HEALTH_MAX_AGE_SECONDS", "300")),
    )
    print(detail)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the health tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/test_healthcheck.py -v`
Expected: PASS for both healthcheck tests

- [ ] **Step 5: Commit the healthcheck**

```bash
git add sms_forwarder/healthcheck.py tests/test_healthcheck.py
git commit -m "feat: add forwarder health checks"
```

### Task 5: Integrate Python Runtime Into The Container And Docs

**Files:**
- Create: `enqueue_sms.py`
- Create: `send_worker.py`
- Create: `check_forwarder_health.py`
- Modify: `gammurc`
- Modify: `entrypoint.sh`
- Modify: `Dockerfile`
- Modify: `Dockerfile.alpine`
- Modify: `README.md`
- Modify: `sms-to-telegram.container`
- Modify: `sms-to-telegram.live.container`
- Delete: `sms_to_telegram.sh`

- [ ] **Step 1: Write the failing integration assertions before touching runtime files**

```python
from pathlib import Path


def test_runtime_files_reference_python_forwarder():
    assert "RunOnReceive=/usr/bin/enqueue_sms.py" in Path("gammurc").read_text()
    entrypoint = Path("entrypoint.sh").read_text()
    assert "/usr/bin/send_worker.py &" in entrypoint
    assert "WORKER_PID_FILE=/var/run/sms-forwarder-worker.pid" in entrypoint
```

Save as `tests/test_runtime_contract.py`.

- [ ] **Step 2: Run the runtime contract test to verify it fails**

Run: `./.venv/bin/python -m pytest tests/test_runtime_contract.py -v`
Expected: FAIL because the current runtime still references `sms_to_telegram.sh`

- [ ] **Step 3: Update the runtime files and documentation**

```ini
; gammurc
RunOnReceive=/usr/bin/enqueue_sms.py
```

```sh
#!/bin/sh
set -eu

originalfile=/etc/gammurc
tmpfile=/etc/gammurc.tmp
queue_root=${QUEUE_ROOT:-/var/spool/sms-forwarder}
worker_pid_file=${WORKER_PID_FILE:-/var/run/sms-forwarder-worker.pid}

cp "$originalfile" "$tmpfile"
envsubst < "$originalfile" > "$tmpfile" && mv "$tmpfile" "$originalfile"

mkdir -p "$queue_root"/pending "$queue_root"/processing "$queue_root"/sent "$queue_root"/failed
/usr/bin/send_worker.py &
worker_pid=$!
printf '%s\n' "$worker_pid" > "$worker_pid_file"

gammu-smsd -c /etc/gammurc -p /var/run/gammu-smsd.pid &
gammu_pid=$!

while kill -0 "$worker_pid" 2>/dev/null && kill -0 "$gammu_pid" 2>/dev/null; do
    sleep 1
done

kill "$worker_pid" "$gammu_pid" 2>/dev/null || true
wait "$worker_pid" "$gammu_pid" 2>/dev/null || true

if ! kill -0 "$worker_pid" 2>/dev/null; then
    echo "worker exited unexpectedly" >&2
    exit 1
fi

exit 1
```

```python
#!/usr/bin/env python3
# enqueue_sms.py
from sms_forwarder.enqueue_hook import main


if __name__ == "__main__":
    raise SystemExit(main())
```

```python
#!/usr/bin/env python3
# send_worker.py
from sms_forwarder.worker import main


if __name__ == "__main__":
    raise SystemExit(main())
```

```python
#!/usr/bin/env python3
# check_forwarder_health.py
from sms_forwarder.healthcheck import main


if __name__ == "__main__":
    raise SystemExit(main())
```

```dockerfile
# Dockerfile runtime changes
RUN apt-get update && \
    apt-get install --no-install-recommends -y gammu-smsd curl gettext locales ca-certificates python3 && \
    localedef -i en_US -c -f UTF-8 -A /usr/share/locale/locale.alias en_US.UTF-8 && \
    apt-get clean && apt-get autoclean && \
    rm -rf /var/lib/apt/lists/*

ENV PYTHONPATH=/opt/sms_forwarder
COPY sms_forwarder /opt/sms_forwarder/sms_forwarder
COPY enqueue_sms.py send_worker.py check_forwarder_health.py /usr/bin/
RUN mkdir -p /var/log/smsd/ /var/spool/sms-forwarder/pending /var/spool/sms-forwarder/processing /var/spool/sms-forwarder/sent /var/spool/sms-forwarder/failed && \
    chmod +x /usr/bin/entrypoint.sh /usr/bin/enqueue_sms.py /usr/bin/send_worker.py /usr/bin/check_forwarder_health.py

HEALTHCHECK CMD /usr/bin/check_forwarder_health.py
```

```dockerfile
# Dockerfile.alpine runtime changes
RUN apk update && \
    apk add libusb \
      libcurl \
      tzdata \
      curl \
      gettext \
      ca-certificates \
      python3

ENV PYTHONPATH=/opt/sms_forwarder
COPY sms_forwarder /opt/sms_forwarder/sms_forwarder
COPY enqueue_sms.py send_worker.py check_forwarder_health.py /usr/bin/
RUN mkdir /var/log/smsd/ && \
    mkdir -p /var/spool/gammu/inbox /var/spool/gammu/outbox /var/spool/gammu/sent /var/spool/gammu/error && \
    mkdir -p /var/spool/sms-forwarder/pending /var/spool/sms-forwarder/processing /var/spool/sms-forwarder/sent /var/spool/sms-forwarder/failed && \
    chmod +x /usr/bin/entrypoint.sh /usr/bin/enqueue_sms.py /usr/bin/send_worker.py /usr/bin/check_forwarder_health.py

HEALTHCHECK CMD /usr/bin/check_forwarder_health.py
```

```ini
# sms-to-telegram.container and sms-to-telegram.live.container
Volume=/var/lib/sms-to-telegram-queue:/var/spool/sms-forwarder:Z
Environment=QUEUE_ROOT=/var/spool/sms-forwarder
Environment=WORKER_PID_FILE=/var/run/sms-forwarder-worker.pid
```

Update `README.md` to document:

- the Python queue worker architecture
- the persistent queue volume requirement
- how to inspect `pending/`, `failed/`, and `sent/`
- how to read health output and `journalctl` events

Delete `sms_to_telegram.sh`.

- [ ] **Step 4: Run the full test suite and verify all tests pass**

Run: `./.venv/bin/python -m pytest -v`
Expected: PASS for queue store, enqueue hook, worker, healthcheck, and runtime contract tests

- [ ] **Step 5: Build both images to verify container integration**

Run: `docker build -t sms-to-telegram:test .`
Expected: image builds successfully with Python runtime and healthcheck

Run: `docker build -f Dockerfile.alpine -t sms-to-telegram:alpine-test .`
Expected: alpine image builds successfully with Python runtime and healthcheck

- [ ] **Step 6: Commit the runtime integration**

```bash
git add enqueue_sms.py send_worker.py check_forwarder_health.py gammurc entrypoint.sh Dockerfile Dockerfile.alpine README.md sms-to-telegram.container sms-to-telegram.live.container tests/test_runtime_contract.py
git rm sms_to_telegram.sh
git commit -m "feat: add durable SMS forwarding runtime"
```
