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
                "entry_fee": p.get("entry_fee"),
                "fee_bps": p.get("fee_bps"),
                "stop": p.get("stop"),
                "opened_at": opened_at,
                "reason": ["current_position"],
                "synthetic": True,
            })
    return sorted(history, key=lambda row: row.get("closed_at") or row.get("opened_at") or 0, reverse=True)


def enrich_positions(positions, default_fee_bps):
    out = {}
    total_pnl = 0
    total_fee = 0
    for symbol, p in positions.items():
        market_status = watcher.symbol_status(symbol)
        mark_error = None
        try:
            price = watcher.mark_price(symbol)
        except Exception as exc:
            price = None
            mark_error = str(exc)
        entry = float(p.get("entry") or 0)
        qty = float(p.get("qty") or 0)
        fee_bps = float(p.get("fee_bps", default_fee_bps) or 0)
        entry_notional = float(p.get("notional") or (entry * qty))
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
            "gross_pnl": gross_pnl,
            "entry_fee": entry_fee,
            "exit_fee": exit_fee,
            "fee": fee,
            "fee_bps": fee_bps,
            "pnl": pnl,
            "pnl_pct": ((pnl / entry_notional) * 100 if pnl is not None and entry_notional else None),
        }
    return out, total_pnl, total_fee


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

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/state":
            fee_bps = float(os.getenv("FEE_BPS", "10"))
            raw_positions = read_json("positions.json", {})
            positions, pnl, fees = enrich_positions(raw_positions, fee_bps)
            equity = float(os.getenv("EQUITY", "1000"))
            return self.json({
                "watchlist": read_json("watchlist.json", {}),
                "positions": positions,
                "account": {"initial": equity, "pnl": pnl, "fees": fees, "fee_bps": fee_bps, "equity": equity + pnl},
            })
        if path == "/api/history":
            return self.json(trade_history(read_json("positions.json", {})))
        if path == "/api/signals":
            args = type("Args", (), {
                "max_symbols": 50,
                "level_kline": os.getenv("LEVEL_KLINE", "15m"),
                "volume_kline": os.getenv("VOLUME_KLINE", "1m"),
                "min_qvol": float(os.getenv("MIN_QVOL", "50000")),
                "vol_mult": float(os.getenv("VOL_MULT", "2")),
                "spike_minutes": int(os.getenv("SPIKE_MINUTES", "3")),
                "setup_only": os.getenv("SETUP_ONLY", "1") != "0",
            })()
            return self.json(strategy.current_signals(args))
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
