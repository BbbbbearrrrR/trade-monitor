#!/usr/bin/env python3
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal, ROUND_DOWN

API = os.getenv("BINANCE_FUTURES_API_BASE", "https://fapi.binance.com")


class BinanceLiveError(RuntimeError):
    pass


def trade_mode():
    return os.getenv("TRADE_MODE", "paper").strip().lower()


def live_enabled():
    return trade_mode() == "live"


def require_live_confirmation():
    if not live_enabled():
        return
    if os.getenv("LIVE_TRADING_CONFIRM") != "YES":
        raise BinanceLiveError("TRADE_MODE=live requires LIVE_TRADING_CONFIRM=YES")


def _decimal(value):
    return Decimal(str(value))


def floor_to_step(value, step):
    step = _decimal(step)
    if step <= 0:
        return _decimal(value)
    return (_decimal(value) / step).to_integral_value(rounding=ROUND_DOWN) * step


def decimal_text(value):
    value = _decimal(value).normalize()
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


class BinanceFuturesClient:
    def __init__(self, api_key=None, api_secret=None, api_base=None):
        self.api_key = api_key or os.getenv("BINANCE_API_KEY") or os.getenv("BINANCE_REAL_API_KEY")
        self.api_secret = api_secret or os.getenv("BINANCE_API_SECRET") or os.getenv("BINANCE_REAL_API_SECRET")
        self.api_base = (api_base or API).rstrip("/")
        self._exchange_info = None
        if not self.api_key or not self.api_secret:
            raise BinanceLiveError("missing BINANCE_API_KEY/BINANCE_API_SECRET")

    def public_request(self, method, path, params=None):
        query = urllib.parse.urlencode(params or {})
        url = self.api_base + path + (("?" + query) if query else "")
        req = urllib.request.Request(url, method=method, headers={"User-Agent": "trade-monitor-live/0.1"})
        return self._read_json(req)

    def signed_request(self, method, path, params=None):
        params = dict(params or {})
        params.setdefault("recvWindow", int(os.getenv("BINANCE_RECV_WINDOW", "5000")))
        params["timestamp"] = int(time.time() * 1000)
        payload = urllib.parse.urlencode(params)
        signature = hmac.new(self.api_secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
        body = (payload + "&signature=" + signature).encode("utf-8")
        headers = {
            "X-MBX-APIKEY": self.api_key,
            "User-Agent": "trade-monitor-live/0.1",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        req = urllib.request.Request(self.api_base + path, data=body, method=method, headers=headers)
        return self._read_json(req)

    def _read_json(self, req):
        try:
            with urllib.request.urlopen(req, timeout=20) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise BinanceLiveError(f"binance http {exc.code}: {detail}") from exc
        except OSError as exc:
            raise BinanceLiveError(str(exc)) from exc
        return json.loads(body) if body else {}

    def exchange_info(self):
        if self._exchange_info is None:
            self._exchange_info = self.public_request("GET", "/fapi/v1/exchangeInfo")
        return self._exchange_info

    def symbol_info(self, symbol):
        for row in self.exchange_info().get("symbols", []):
            if row.get("symbol") == symbol:
                return row
        raise BinanceLiveError(f"{symbol} not found in exchangeInfo")

    def symbol_filters(self, symbol):
        return {f.get("filterType"): f for f in self.symbol_info(symbol).get("filters", [])}

    def market_quantity(self, symbol, raw_qty, price=None):
        filters = self.symbol_filters(symbol)
        lot = filters.get("MARKET_LOT_SIZE") or filters.get("LOT_SIZE") or {}
        step = lot.get("stepSize", "0")
        min_qty = _decimal(lot.get("minQty", "0"))
        max_qty = _decimal(lot.get("maxQty", "0"))
        qty = floor_to_step(raw_qty, step)
        if qty <= 0 or qty < min_qty:
            raise BinanceLiveError(f"{symbol} quantity {decimal_text(qty)} below minQty {decimal_text(min_qty)}")
        if max_qty > 0 and qty > max_qty:
            raise BinanceLiveError(f"{symbol} quantity {decimal_text(qty)} above maxQty {decimal_text(max_qty)}")
        min_notional = filters.get("MIN_NOTIONAL", {}).get("notional")
        if min_notional and price is not None and qty * _decimal(price) < _decimal(min_notional):
            raise BinanceLiveError(f"{symbol} notional below minNotional {min_notional}")
        return qty

    def change_leverage(self, symbol, leverage):
        leverage = int(float(leverage))
        return self.signed_request("POST", "/fapi/v1/leverage", {"symbol": symbol, "leverage": leverage})

    def market_order(self, symbol, side, quantity, reduce_only=False, client_order_id=None):
        params = {
            "symbol": symbol,
            "side": side,
            "type": "MARKET",
            "quantity": decimal_text(quantity),
            "newOrderRespType": "RESULT",
        }
        if reduce_only:
            params["reduceOnly"] = "true"
        if client_order_id:
            params["newClientOrderId"] = client_order_id
        return self.signed_request("POST", "/fapi/v1/order", params)


def _max_live_notional():
    value = os.getenv("MAX_LIVE_NOTIONAL")
    return float(value) if value else None


def _allowed_symbol(symbol):
    raw = os.getenv("LIVE_SYMBOLS", "").strip()
    if not raw:
        return True
    return symbol in {item.strip() for item in raw.split(",") if item.strip()}


def open_long(order, client=None):
    require_live_confirmation()
    symbol = order["symbol"]
    if not _allowed_symbol(symbol):
        raise BinanceLiveError(f"{symbol} not in LIVE_SYMBOLS")
    max_notional = _max_live_notional()
    if max_notional is not None and float(order.get("notional") or 0) > max_notional:
        raise BinanceLiveError(f"{symbol} notional {order.get('notional')} exceeds MAX_LIVE_NOTIONAL={max_notional}")
    client = client or BinanceFuturesClient()
    client.change_leverage(symbol, order.get("leverage", 1))
    qty = client.market_quantity(symbol, order["qty"], order.get("price"))
    response = client.market_order(symbol, "BUY", qty, client_order_id=f"tm_open_{symbol}_{int(time.time())}")
    return normalize_open_response(order, response, qty)


def open_short(order, client=None):
    require_live_confirmation()
    symbol = order["symbol"]
    if not _allowed_symbol(symbol):
        raise BinanceLiveError(f"{symbol} not in LIVE_SYMBOLS")
    max_notional = _max_live_notional()
    if max_notional is not None and float(order.get("notional") or 0) > max_notional:
        raise BinanceLiveError(f"{symbol} notional {order.get('notional')} exceeds MAX_LIVE_NOTIONAL={max_notional}")
    client = client or BinanceFuturesClient()
    client.change_leverage(symbol, order.get("leverage", 1))
    qty = client.market_quantity(symbol, order["qty"], order.get("price"))
    response = client.market_order(symbol, "SELL", qty, client_order_id=f"tm_open_{symbol}_{int(time.time())}")
    return normalize_open_response(order, response, qty)


def close_long(symbol, qty, price=None, client=None):
    require_live_confirmation()
    if not _allowed_symbol(symbol):
        raise BinanceLiveError(f"{symbol} not in LIVE_SYMBOLS")
    client = client or BinanceFuturesClient()
    live_qty = client.market_quantity(symbol, qty, price)
    response = client.market_order(symbol, "SELL", live_qty, reduce_only=True, client_order_id=f"tm_close_{symbol}_{int(time.time())}")
    return normalize_close_response(response, live_qty)


def close_short(symbol, qty, price=None, client=None):
    require_live_confirmation()
    if not _allowed_symbol(symbol):
        raise BinanceLiveError(f"{symbol} not in LIVE_SYMBOLS")
    client = client or BinanceFuturesClient()
    live_qty = client.market_quantity(symbol, qty, price)
    response = client.market_order(symbol, "BUY", live_qty, reduce_only=True, client_order_id=f"tm_close_{symbol}_{int(time.time())}")
    return normalize_close_response(response, live_qty)


def normalize_open_response(order, response, qty):
    executed_qty = float(response.get("executedQty") or qty)
    avg_price = float(response.get("avgPrice") or 0) or float(order.get("price") or 0)
    cum_quote = float(response.get("cumQuote") or 0) or avg_price * executed_qty
    return {
        **order,
        "price": avg_price,
        "qty": executed_qty,
        "notional": round(cum_quote, 8),
        "margin": round(cum_quote / max(1.0, float(order.get("leverage") or 1)), 8),
        "live": True,
        "exchange": "binance_usdm",
        "order_id": response.get("orderId"),
        "client_order_id": response.get("clientOrderId"),
        "status": response.get("status"),
        "raw_order": response,
    }


def normalize_close_response(response, qty):
    executed_qty = float(response.get("executedQty") or qty)
    avg_price = float(response.get("avgPrice") or 0)
    cum_quote = float(response.get("cumQuote") or 0)
    return {
        "live": True,
        "exchange": "binance_usdm",
        "order_id": response.get("orderId"),
        "client_order_id": response.get("clientOrderId"),
        "status": response.get("status"),
        "executed_qty": executed_qty,
        "avg_price": avg_price or None,
        "cum_quote": cum_quote or None,
        "raw_order": response,
    }


def demo():
    assert decimal_text(floor_to_step("1.239", "0.01")) == "1.23"
    assert decimal_text(floor_to_step("7", "1")) == "7"
    assert trade_mode() in ("paper", "live")
    print("demo ok")


if __name__ == "__main__":
    demo()
