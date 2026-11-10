import json
from http.client import HTTPException
from urllib.error import HTTPError
from urllib.request import Request, urlopen


class TelegramError(Exception):
    def __init__(self, code, retry_after=0):
        super().__init__(f"Telegram API error {code}")
        self.code = code
        self.retry_after = retry_after


class Telegram:
    def __init__(self, token):
        self.base_url = f"https://api.telegram.org/bot{token}/"

    def call(self, method, **params):
        request = Request(
            self.base_url + method,
            data=json.dumps(params).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        network_timeout = params.get("timeout", 0) + 8
        try:
            with urlopen(request, timeout=network_timeout) as response:
                data = json.load(response)
        except HTTPError as exc:
            with exc:
                try:
                    body = json.load(exc)
                    retry_after = int(body.get("parameters", {}).get("retry_after", 0))
                except (ValueError, TypeError, AttributeError, OSError, HTTPException):
                    retry_after = 0
            raise TelegramError(exc.code, retry_after) from None
        except (OSError, ValueError, HTTPException):
            # urllib exceptions can include the request URL, which contains the token.
            raise TelegramError(503) from None

        if not isinstance(data, dict):
            raise TelegramError(503)
        if not data.get("ok"):
            raise TelegramError(data.get("error_code", 503), data.get("parameters", {}).get("retry_after", 0))
        return data["result"]
