"""
Minimal Binance USDⓈ-M Futures client.

Two domains are used on purpose:
  * ORDERS / ACCOUNT  -> testnet  (https://testnet.binancefuture.com) by default.
    This is where real (paper) orders are placed. Never points at prod unless
    you explicitly change base_url AND flip the safety flag in the bot.
  * LONG/SHORT RATIO  -> production data endpoint (fapi.binance.com/futures/data).
    Testnet does not serve the global account long/short ratio, and it is a
    public, no-auth statistic, so we read the real market signal from prod and
    trade it on testnet.

Only the handful of endpoints the bot needs are implemented.
"""
from __future__ import annotations

import hashlib
import hmac
import time
from urllib.parse import urlencode

import requests

TESTNET = "https://testnet.binancefuture.com"
PROD_DATA = "https://fapi.binance.com"      # public futures/data (ratio) only


class BinanceFutures:
    def __init__(self, api_key: str = "", api_secret: str = "",
                 base_url: str = TESTNET, recv_window: int = 5000):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.api_secret = api_secret.encode()
        self.recv_window = recv_window
        self.s = requests.Session()
        if api_key:
            self.s.headers.update({"X-MBX-APIKEY": api_key})

    # --- low level --------------------------------------------------------- #
    def _sign(self, params: dict) -> str:
        params["timestamp"] = int(time.time() * 1000)
        params["recvWindow"] = self.recv_window
        query = urlencode(params)
        sig = hmac.new(self.api_secret, query.encode(), hashlib.sha256).hexdigest()
        return f"{query}&signature={sig}"

    def _get(self, path: str, params: dict | None = None, signed: bool = False, base: str | None = None):
        base = base or self.base_url
        url = f"{base}{path}"
        if signed:
            url = f"{url}?{self._sign(params or {})}"
            r = self.s.get(url, timeout=30)
        else:
            r = self.s.get(url, params=params or {}, timeout=30)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, params: dict):
        url = f"{self.base_url}{path}?{self._sign(params)}"
        r = self.s.post(url, timeout=30)
        r.raise_for_status()
        return r.json()

    # --- public data ------------------------------------------------------- #
    def klines(self, symbol: str, interval: str = "1h", limit: int = 500) -> list:
        return self._get("/fapi/v1/klines", {"symbol": symbol, "interval": interval, "limit": limit})

    def long_short_ratio(self, symbol: str, period: str = "1h", limit: int = 500) -> list:
        """Global long/short ACCOUNT ratio — read from the production data host."""
        return self._get("/futures/data/globalLongShortAccountRatio",
                         {"symbol": symbol, "period": period, "limit": limit}, base=PROD_DATA)

    def mark_price(self, symbol: str) -> float:
        return float(self._get("/fapi/v1/premiumIndex", {"symbol": symbol})["markPrice"])

    def step_size(self, symbol: str) -> float:
        info = self._get("/fapi/v1/exchangeInfo")
        for s in info["symbols"]:
            if s["symbol"] == symbol:
                for f in s["filters"]:
                    if f["filterType"] == "LOT_SIZE":
                        return float(f["stepSize"])
        return 0.001

    # --- account / trading (signed, testnet) ------------------------------- #
    def position(self, symbol: str) -> dict:
        for p in self._get("/fapi/v2/positionRisk", {"symbol": symbol}, signed=True):
            if p["symbol"] == symbol:
                return p
        return {}

    def position_amt(self, symbol: str) -> float:
        p = self.position(symbol)
        return float(p.get("positionAmt", 0.0)) if p else 0.0

    def market_order(self, symbol: str, side: str, qty: float, reduce_only: bool = False) -> dict:
        params = {"symbol": symbol, "side": side, "type": "MARKET", "quantity": qty}
        if reduce_only:
            params["reduceOnly"] = "true"
        return self._post("/fapi/v1/order", params)
