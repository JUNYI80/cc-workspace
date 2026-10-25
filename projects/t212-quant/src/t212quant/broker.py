"""Minimal Trading 212 Public API (v0) client.

Docs: https://docs.trading212.com/api
- Works for Invest and Stocks ISA accounts only (not CFD).
- Auth: HTTP Basic with API key as username and API secret as password
  (generate both in the app under Settings > API).
- In the live environment only market orders are supported. Sells are
  market orders with a negative quantity.
- Rate limits are per account and reported in x-ratelimit-* headers.
"""
from __future__ import annotations

import base64
import logging
import os
import time
from typing import Any, Callable

import requests

log = logging.getLogger(__name__)

ENVIRONMENTS = ("demo", "live")
MAX_RETRIES = 3


class T212Error(RuntimeError):
    def __init__(self, status: int, body: str, path: str):
        super().__init__(f"Trading 212 API error {status} on {path}: {body[:500]}")
        self.status = status
        self.body = body


class Trading212Client:
    def __init__(
        self,
        api_key: str,
        api_secret: str | None,
        env: str = "demo",
        session: requests.Session | None = None,
        timeout: float = 30,
        sleep: Callable[[float], None] = time.sleep,
    ):
        if env not in ENVIRONMENTS:
            raise ValueError(f"env must be one of {ENVIRONMENTS}, got {env!r}")
        if not api_key:
            raise ValueError("Missing Trading 212 API key")
        self.env = env
        self.base_url = f"https://{env}.trading212.com/api/v0"
        self.session = session or requests.Session()
        if api_secret:
            token = base64.b64encode(f"{api_key}:{api_secret}".encode()).decode()
            auth = f"Basic {token}"
        else:  # legacy keys created before key+secret pairs existed
            auth = api_key
        self.session.headers.update({"Authorization": auth, "Content-Type": "application/json"})
        self.timeout = timeout
        self._sleep = sleep
        self._limits: dict[str, tuple[int, float]] = {}  # path -> (remaining, reset epoch)

    @classmethod
    def from_env(cls, env: str = "demo", **kw) -> "Trading212Client":
        """Read T212_API_KEY / T212_API_SECRET (optionally suffixed _DEMO or _LIVE)."""
        suffix = env.upper()
        key = os.getenv(f"T212_API_KEY_{suffix}") or os.getenv("T212_API_KEY")
        secret = os.getenv(f"T212_API_SECRET_{suffix}") or os.getenv("T212_API_SECRET")
        if not key:
            raise RuntimeError(
                f"Set T212_API_KEY (and T212_API_SECRET) or T212_API_KEY_{suffix} in your environment"
            )
        return cls(key, secret, env=env, **kw)

    # -- HTTP plumbing -------------------------------------------------------

    def _request(self, method: str, path: str, **kw) -> Any:
        limit_key = path.split("?")[0]
        for attempt in range(MAX_RETRIES):
            remaining, reset = self._limits.get(limit_key, (1, 0.0))
            if remaining <= 0:
                wait = reset - time.time()
                if wait > 0:
                    log.info("Rate limit on %s, waiting %.1fs", limit_key, wait)
                    self._sleep(min(wait + 0.5, 60))
            resp = self.session.request(method, self.base_url + path, timeout=self.timeout, **kw)
            self._record_limits(limit_key, resp.headers)
            if resp.status_code == 429 and attempt < MAX_RETRIES - 1:
                reset = float(resp.headers.get("x-ratelimit-reset", 0) or 0)
                self._sleep(min(max(reset - time.time(), 1.0) + 0.5, 60))
                continue
            if resp.status_code >= 400:
                raise T212Error(resp.status_code, resp.text, path)
            if resp.status_code == 204 or not resp.content:
                return None
            return resp.json()
        raise T212Error(429, "rate limited after retries", path)

    def _record_limits(self, key: str, headers) -> None:
        try:
            remaining = int(headers.get("x-ratelimit-remaining"))
            reset = float(headers.get("x-ratelimit-reset"))
        except (TypeError, ValueError):
            return
        self._limits[key] = (remaining, reset)

    # -- Account -------------------------------------------------------------

    def account_info(self) -> dict:
        return self._request("GET", "/equity/account/info")

    def cash(self) -> dict:
        """{free, invested, total, ppl, result, blocked, pieCash} in account currency."""
        return self._request("GET", "/equity/account/cash")

    def portfolio(self) -> list[dict]:
        """Open positions: [{ticker, quantity, averagePrice, currentPrice, ppl, ...}]."""
        return self._request("GET", "/equity/portfolio")

    def instruments(self) -> list[dict]:
        """All tradable instruments: [{ticker, name, isin, currencyCode, type, ...}]."""
        return self._request("GET", "/equity/metadata/instruments")

    # -- Orders --------------------------------------------------------------

    def pending_orders(self) -> list[dict]:
        return self._request("GET", "/equity/orders")

    def order(self, order_id: int) -> dict:
        return self._request("GET", f"/equity/orders/{order_id}")

    def place_market_order(self, ticker: str, quantity: float) -> dict:
        """Positive quantity buys, negative quantity sells."""
        if quantity == 0:
            raise ValueError("quantity must be non-zero")
        return self._request(
            "POST", "/equity/orders/market", json={"ticker": ticker, "quantity": quantity}
        )

    def cancel_order(self, order_id: int) -> None:
        self._request("DELETE", f"/equity/orders/{order_id}")
