"""토스증권 실시간 체결(trade:kr) 웹소켓 클라이언트. Streamlit에 의존하지 않습니다.

- 연결 1개로 종목 최대 100개의 체결가를 받아 메모리에 보관합니다.
- 끊기면 지수 백오프(1s→2s→4s… 최대 60s)로 자동 재연결하고 구독을 다시 선언합니다.
- 스펙: wss://openapi-ws.tossinvest.com/ws/v1 (Authorization: Bearer 토큰, handshake 1회 인증)
"""

import json
import random
import threading
import time

import websocket  # pip install websocket-client

WS_URL = "wss://openapi-ws.tossinvest.com/ws/v1"
MAX_CODES = 100  # 연결당 구독 한도 (채널 x 종목 합산)


class TossFeed:
    def __init__(self, token_getter):
        """token_getter: 호출하면 access token 문자열을 돌려주는 함수."""

        self._token_getter = token_getter
        self._lock = threading.Lock()

        self._symbols = ()       # 구독하려는 종목
        self._prices = {}        # symbol -> (price, 수신 시각)
        self._ws = None
        self._connected = False
        self._last_msg = 0.0     # 마지막 체결 수신 시각
        self._last_error = ""
        self._rejected = []
        self._subscribed = 0
        self._stopped = False

        threading.Thread(target=self._run, daemon=True, name="toss-feed").start()

    # ------------------------------------------------------------------
    # 외부에서 쓰는 함수
    # ------------------------------------------------------------------
    def set_symbols(self, symbols):
        """구독 종목을 바꿉니다. 같으면 아무것도 하지 않습니다."""

        symbols = tuple(symbols)[:MAX_CODES]

        with self._lock:
            if symbols == self._symbols:
                return

            keep = set(symbols)
            self._symbols = symbols
            self._prices = {k: v for k, v in self._prices.items() if k in keep}

        self._declare()

    def get_prices(self, symbols):
        with self._lock:
            return {s: self._prices[s][0] for s in symbols if s in self._prices}

    def status(self):
        with self._lock:
            age = (time.time() - self._last_msg) if self._last_msg else None

            return {
                "connected": self._connected,
                "age": age,
                "priced": len(self._prices),
                "subscribed": self._subscribed,
                "wanted": len(self._symbols),
                "rejected": list(self._rejected),
                "error": self._last_error,
            }

    # ------------------------------------------------------------------
    # 내부 동작
    # ------------------------------------------------------------------
    def _set_error(self, text):
        with self._lock:
            self._last_error = str(text)[:200]

    def _declare(self):
        """구독 선언 (선언형 full-replace: 이 배열이 곧 현재 구독 전체)."""

        with self._lock:
            ws = self._ws
            connected = self._connected
            symbols = list(self._symbols)

        if not (ws and connected):
            return  # 연결되면 _on_open에서 선언합니다

        payload = [{"id": "cap"}, {"type": "trade:kr", "codes": symbols}] if symbols else []

        try:
            ws.send(json.dumps(payload))
        except Exception as error:
            self._set_error(f"구독 선언 실패: {error}")

    def _run(self):
        backoff = 1.0

        while not self._stopped:
            started = time.time()

            try:
                token = self._token_getter()

                ws = websocket.WebSocketApp(
                    WS_URL,
                    header=[f"Authorization: Bearer {token}"],
                    on_open=self._on_open,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close,
                )

                self._ws = ws
                ws.run_forever(ping_interval=30, ping_timeout=10)

            except Exception as error:
                self._set_error(f"{type(error).__name__}: {error}")

            with self._lock:
                self._connected = False

            if time.time() - started > 60:
                backoff = 1.0  # 오래 유지되다 끊긴 경우는 바로 재시도

            time.sleep(backoff + random.random())
            backoff = min(backoff * 2, 60.0)

    def _on_open(self, ws):
        with self._lock:
            self._connected = True
            self._last_error = ""

        self._declare()

        threading.Thread(
            target=self._keepalive, args=(ws,), daemon=True, name="toss-feed-ping"
        ).start()

    def _keepalive(self, ws):
        """서버는 클라이언트 수신이 180초 없으면 끊으므로 텍스트 PING을 주기적으로 보냅니다."""

        while ws is self._ws and not self._stopped:
            time.sleep(55)

            try:
                if ws is self._ws and self._connected:
                    ws.send("PING")
            except Exception:
                break

    def _on_message(self, ws, message):
        try:
            frame = json.loads(message)
        except ValueError:
            return  # pong 등 JSON이 아닌 프레임

        kind = frame.get("type")

        if kind == "message":
            topic = str(frame.get("topic", ""))

            if not topic.startswith("trade:kr:"):
                return

            try:
                price = float(frame["data"]["price"])
            except (KeyError, TypeError, ValueError):
                return

            symbol = topic.split(":", 2)[2]
            now = time.time()

            with self._lock:
                self._prices[symbol] = (price, now)
                self._last_msg = now

        elif kind == "subscriptions":
            with self._lock:
                self._subscribed = len(frame.get("subscribed", []))
                self._rejected = frame.get("rejected", [])

        elif kind == "error":
            error = frame.get("error") or {}
            self._set_error(f"{error.get('code')}: {error.get('message')}")

            if error.get("code") == "server-shutdown":
                ws.close()  # 루프가 백오프 후 재연결하고 구독을 다시 선언합니다

    def _on_error(self, ws, error):
        self._set_error(f"{type(error).__name__}: {error}")

    def _on_close(self, ws, code, reason):
        with self._lock:
            self._connected = False
