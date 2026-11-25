"""SIHWANG Oracle API client."""

import os
import time
import requests


class TossClientError(Exception):
    pass


def _secret(key):
    value = os.getenv(key)

    if value:
        return value

    try:
        import streamlit as st
        return str(st.secrets.get(key, ""))
    except Exception:
        return ""


def api_get(path, params=None):

    base = _secret("MTNW_API_URL").rstrip("/")
    key = _secret("MTNW_API_KEY")

    if not base or not key:
        raise TossClientError(
            "MTNW_API_URL / MTNW_API_KEY missing"
        )

    if not base.startswith("https://"):
        raise TossClientError(
            "HTTPS connection required"
        )

    payload = {
        "path": path,
        "params": params or {}
    }

    headers = {
        "X-API-Key": key
    }

    for attempt in range(3):

        try:
            response = requests.post(
                base + "/v1/market/query",
                headers=headers,
                json=payload,
                timeout=(8, 65),
                allow_redirects=False
            )

        except requests.exceptions.SSLError:
            raise TossClientError(
                "SSL certificate verification failed"
            ) from None

        except requests.RequestException:
            if attempt == 2:
                raise TossClientError(
                    f"{path} connection failed"
                ) from None

            time.sleep(2 ** attempt)
            continue

        if response.status_code == 200:
            data = response.json()

            if "result" not in data:
                raise TossClientError(
                    "Invalid backend response"
                )

            return data["result"]

        if response.status_code in (429, 500, 502, 503, 504):
            if attempt < 2:
                time.sleep(2 ** attempt)
                continue

        raise TossClientError(
            f"{path} HTTP {response.status_code}"
        )

    raise TossClientError("API request failed")
