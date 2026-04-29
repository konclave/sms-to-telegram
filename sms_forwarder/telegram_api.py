from __future__ import annotations

from dataclasses import dataclass
import json
from urllib import error, request


class RetryableDeliveryError(RuntimeError):
    pass


class TerminalDeliveryError(RuntimeError):
    pass


@dataclass
class TelegramClient:
    bot_token: str
    timeout: float = 10.0
    api_base: str = "https://api.telegram.org"

    def send_message(self, payload: dict) -> None:
        body = json.dumps(
            {
                "chat_id": payload["telegram_chat_id"],
                "text": f'{payload["sender"]}:\n{payload["text"]}',
            }
        ).encode("utf-8")
        req = request.Request(
            url=f"{self.api_base}/bot{self.bot_token}/sendMessage",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=self.timeout) as response:
                raw_body = response.read()
        except error.HTTPError as exc:
            message = self._error_message(exc)
            if exc.code == 429 or 500 <= exc.code <= 599:
                raise RetryableDeliveryError(message) from exc
            if exc.code in {400, 401, 403, 404}:
                raise TerminalDeliveryError(message) from exc
            raise RetryableDeliveryError(message) from exc
        except error.URLError as exc:
            raise RetryableDeliveryError(str(exc.reason)) from exc

        data = json.loads(raw_body.decode("utf-8"))
        if data.get("ok"):
            return

        description = data.get("description", "telegram API error")
        if self._is_terminal_description(description):
            raise TerminalDeliveryError(description)
        raise RetryableDeliveryError(description)

    def _error_message(self, exc: error.HTTPError) -> str:
        try:
            payload = json.loads(exc.read().decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return str(exc)
        return payload.get("description", str(exc))

    def _is_terminal_description(self, description: str) -> bool:
        lowered = description.lower()
        terminal_markers = (
            "unauthorized",
            "chat not found",
            "forbidden",
            "bot was blocked",
            "user is deactivated",
        )
        return any(marker in lowered for marker in terminal_markers)
