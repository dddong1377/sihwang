"""Oracle remote realtime feed client."""

import requests
from toss_client import _secret


class RemoteFeed:

    def __init__(self):
        self._symbols = None
        self._error = ""

    def _request(self, method, path, payload=None):
        base = _secret("MTNW_API_URL").rstrip("/")
        key = _secret("MTNW_API_KEY")

        if not base.startswith("https://") or not key:
            raise RuntimeError("Oracle API configuration missing")

        response = requests.request(
            method,
            base + path,
            headers={"X-API-Key": key},
            json=payload,
            timeout=(5, 10),
            allow_redirects=False
        )

        response.raise_for_status()
        return response.json()

    def set_symbols(self, symbols):
        symbols = tuple(dict.fromkeys(symbols))[:100]

        try:
            self._request(
                "POST",
                "/v1/live/subscribe",
                {"symbols": list(symbols)}
            )

            self._symbols = symbols
            self._error = ""

        except Exception as error:
            self._error = type(error).__name__

    def get_prices(self, symbols):
        try:
            result = self._request(
                "POST",
                "/v1/live/prices",
                {"symbols": list(symbols)[:100]}
            )

            return result.get("result", {})

        except Exception as error:
            self._error = type(error).__name__
            return {}

    def status(self):
        try:
            result = self._request(
                "GET",
                "/v1/live/status"
            )

            if self._error:
                result["error"] = self._error

            return result

        except Exception as error:
            return {
                "connected": False,
                "age": None,
                "priced": 0,
                "subscribed": 0,
                "wanted": len(self._symbols or ()),
                "rejected": [],
                "error": type(error).__name__
            }
