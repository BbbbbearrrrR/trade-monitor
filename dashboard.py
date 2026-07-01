#!/usr/bin/env python3
import json
import os
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import strategy
import watcher

ROOT = Path(__file__).parent


def read_json(path, default):
    path = ROOT / path
    return json.loads(path.read_text("utf-8")) if path.exists() else default


def fee_usdt(notional, fee_bps):
    return round(float(notional) * float(fee_bps) / 10000.0, 8)


def mark_prices():
    rows = watcher.get_json("/fapi/v1/ticker/price")
    return {row["symbol"]: float(row["price"]) for row in rows if row.get("symbol") and row.get("price")}


def trade_history(positions):
    history = read_json("trade_history.json", [])
    seen_opens = {
        (row.get("symbol"), row.get("opened_at"))
        for row in history
        if row.get("action") in ("BUY", "OPEN") and row.get("opened_at")
    }
    for symbol, p in positions.items():
        opened_at = p.get("opened_at")
        if opened_at and (symbol, opened_at) not in seen_opens:
            history.append({
                "action": "BUY",
                "symbol": symbol,
                "price": p.get("entry"),
                "qty": p.get("qty"),
                "notional": p.get("notional"),
                "margin": p.get("margin"),
                "entry_fee": p.get("entry_fee"),
                "fee_bps": p.get("fee_bps"),
                "leverage": p.get("leverage"),
                "stop": p.get("stop"),
                "take_profit_1": p.get("take_profit_1"),
                "take_profit_2": p.get("take_profit_2"),
                "take_profit_qty_pct": p.get("take_profit_qty_pct"),
                "opened_at": opened_at,
                "reason": ["current_position"],
                "synthetic": True,
            })
    return sorted(history, key=lambda row: row.get("closed_at") or row.get("opened_at") or 0, reverse=True)


def realized_totals(history, default_fee_bps):
    opens = {}
    realized_pnl = 0
    realized_fee = 0
    for row in sorted(history, key=lambda item: item.get("closed_at") or item.get("opened_at") or 0):
        symbol = row.get("symbol")
        action = row.get("action")
        if action in ("BUY", "OPEN") and symbol:
            opens.setdefault(symbol, []).append(row)
            continue
        if action != "CLOSE":
            continue

        fee_bps = float(row.get("fee_bps", default_fee_bps) or 0)
        exit_notional = float(row.get("notional") or 0)
        exit_fee = float(row.get("exit_fee") or fee_usdt(exit_notional, fee_bps))
        entry_fee = float(row.get("entry_fee") or 0)
        if not entry_fee and symbol and opens.get(symbol):
            entry_fee = float(opens[symbol].pop(0).get("entry_fee") or 0)

        fee = float(row.get("fee") or (entry_fee + exit_fee))
        gross_pnl = float(row.get("gross_pnl") or 0)
        realized_fee += fee
        realized_pnl += gross_pnl - fee
    return realized_pnl, realized_fee


def enrich_positions(positions, default_fee_bps):
    out = {}
    total_pnl = 0
    total_fee = 0
    prices = {}
    prices_error = None
    try:
        prices = mark_prices()
    except Exception as exc:
        prices_error = str(exc)
    for symbol, p in positions.items():
        market_status = watcher.symbol_status(symbol)
        mark_error = None
        price = prices.get(symbol)
        if price is None:
            price = None
            mark_error = prices_error or f"{symbol} has no futures ticker price; status={market_status}"
        entry = float(p.get("entry") or 0)
        qty = float(p.get("qty") or 0)
        fee_bps = float(p.get("fee_bps", default_fee_bps) or 0)
        entry_notional = float(p.get("notional") or (entry * qty))
        leverage = max(1.0, float(p.get("leverage") or 1))
        margin = float(p.get("margin") or (entry_notional / leverage if leverage else entry_notional))
        entry_fee = float(p.get("entry_fee") or fee_usdt(entry_notional, fee_bps))
        if price is None:
            exit_fee = None
            fee = entry_fee
            gross_pnl = None
            pnl = None
        else:
            mark_notional = price * qty
            exit_fee = fee_usdt(mark_notional, fee_bps)
            fee = entry_fee + exit_fee
            gross_pnl = (price - entry) * qty
            pnl = gross_pnl - fee
            total_pnl += pnl
        total_fee += fee
        out[symbol] = {
            **p,
            "mark": price,
            "market_status": market_status,
            "mark_error": mark_error,
            "notional": round(entry_notional, 8),
            "margin": round(margin, 8),
            "leverage": int(leverage) if leverage.is_integer() else leverage,
            "gross_pnl": gross_pnl,
            "entry_fee": entry_fee,
            "exit_fee": exit_fee,
            "fee": fee,
            "fee_bps": fee_bps,
            "pnl": pnl,
            "pnl_pct": ((pnl / entry_notional) * 100 if pnl is not None and entry_notional else None),
        }
    return out, total_pnl, total_fee


def apply_exit_signals(signals):
    watch = watcher.read_json(watcher.WATCHLIST, {})
    positions = watcher.read_json(watcher.POSITIONS, {})
    visible = []
    changed = False
    for signal in signals:
        if signal.get("action") != "EXIT":
            visible.append(signal)
            continue
        symbol = signal.get("symbol")
        if not symbol:
            continue
        if symbol in positions:
            order = watcher.execute_exit(signal, watch, positions, persist=False)
            if order:
                changed = True
            else:
                visible.append(signal)
        elif symbol in watch:
            watch.pop(symbol, None)
            changed = True
    if changed:
        watcher.write_json(watcher.WATCHLIST, watch)
        watcher.write_json(watcher.POSITIONS, positions)
    return visible


def signal_snapshot():
    snapshot = watcher.read_json(watcher.SIGNALS, None)
    if isinstance(snapshot, dict) and isinstance(snapshot.get("signals"), list):
        return snapshot["signals"]
    return None


def state_payload():
    fee_bps = float(os.getenv("FEE_BPS", "10"))
    raw_positions = read_json("positions.json", {})
    positions, unrealized_pnl, unrealized_fees = enrich_positions(raw_positions, fee_bps)
    realized_pnl, realized_fees = realized_totals(read_json("trade_history.json", []), fee_bps)
    pnl = realized_pnl + unrealized_pnl
    fees = realized_fees + unrealized_fees
    equity = float(os.getenv("EQUITY", "1000"))
    slots = int(os.getenv("SLOTS", "10"))
    return {
        "watchlist": read_json("watchlist.json", {}),
        "positions": positions,
        "account": {
            "initial": equity,
            "slots": slots,
            "pnl": pnl,
            "realized_pnl": realized_pnl,
            "unrealized_pnl": unrealized_pnl,
            "fees": fees,
            "realized_fees": realized_fees,
            "unrealized_fees": unrealized_fees,
            "fee_bps": fee_bps,
            "equity": equity + pnl,
        },
    }


def signals_payload():
    signals = signal_snapshot()
    if signals is None:
        args = type("Args", (), {
            "max_symbols": 50,
            "level_kline": os.getenv("LEVEL_KLINE", "15m"),
            "volume_kline": os.getenv("VOLUME_KLINE", "1m"),
            "min_qvol": float(os.getenv("MIN_QVOL", "50000")),
            "vol_mult": float(os.getenv("VOL_MULT", "2")),
            "spike_minutes": int(os.getenv("SPIKE_MINUTES", "3")),
            "breakout_buffer_pct": float(os.getenv("BREAKOUT_BUFFER_PCT", "0.2")),
            "setup_only": os.getenv("SETUP_ONLY", "1") != "0",
        })()
        signals = strategy.current_signals(args)
    return apply_exit_signals(signals)


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT / "web"), **kwargs)

    def json(self, value):
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store, max-age=0")
        super().end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/dashboard":
            state = state_payload()
            return self.json({
                "state": state,
                "history": trade_history(state["positions"]),
                "signals": signals_payload(),
                "updated_at": int(__import__("time").time()),
            })
        if path == "/api/state":
            return self.json(state_payload())
        if path == "/api/history":
            return self.json(trade_history(read_json("positions.json", {})))
        if path == "/api/signals":
            return self.json(signals_payload())
        if path == "/api/klines":
            query = dict(__import__("urllib.parse").parse.parse_qsl(parsed.query))
            symbol = (query.get("symbol") or "BTCUSDT").upper()
            interval = query.get("interval") or "5m"
            limit = max(20, min(int(query.get("limit") or 96), 300))
            try:
                status = watcher.symbol_status(symbol)
                if status != "TRADING":
                    return self.json({"error": f"{symbol} futures contract is {status}", "symbol": symbol, "status": status})
                rows = watcher.bars(symbol, interval, limit)
                if rows and not any(r.get("trades") or r.get("qvol") for r in rows):
                    return self.json({"error": f"{symbol} has no traded candles in this window", "symbol": symbol, "status": status})
                levels = watcher.structure(rows[-16:-1]) if len(rows) >= 16 else {"support": None, "resistance": None}
                return self.json({"symbol": symbol, "interval": interval, "status": status, "rows": rows, "levels": levels})
            except Exception as exc:
                return self.json({"error": str(exc), "symbol": symbol})
        return super().do_GET()


def main():
    ThreadingHTTPServer(("0.0.0.0", int(os.getenv("PORT", "5050"))), Handler).serve_forever()


if __name__ == "__main__":
    main()
