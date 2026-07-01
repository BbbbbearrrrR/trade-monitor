#!/usr/bin/env python3
import argparse
import json
import os
import sys
import time
from pathlib import Path

import watcher

POSITIONS = Path("positions.json")


def fee_usdt(notional, fee_bps):
    return round(float(notional) * float(fee_bps) / 10000.0, 8)


def read_json(path, default):
    return json.loads(path.read_text("utf-8")) if path.exists() else default


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), "utf-8")


def orders(candidates, equity, slots, stop_buffer, positions=None, fee_bps=10):
    open_slots = max(0, slots - len(positions or {}))
    buys = list(candidates)[:open_slots]
    if not buys:
        return []
    cash = equity / slots
    return [
        {
            "action": "BUY",
            "symbol": s["symbol"],
            "price": s["price"],
            "notional": round(cash, 2),
            "qty": round(cash / s["price"], 8),
            "entry_fee": fee_usdt(cash, fee_bps),
            "fee_bps": float(fee_bps),
            "leverage": 1,
            "stop": round(s["support"] * (1 - stop_buffer), 8) if s.get("support") else None,
            "take_profit_1": round(s["price"] * 1.25, 8),
            "take_profit_2": round(s["price"] * 1.50, 8),
            "take_profit_qty_pct": [50, 50],
        }
        for s in buys
    ]


def read_signals(stdin):
    out = []
    for line in stdin:
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        out.append(json.loads(line))
    return out


def current_signals(args):
    return watcher.current_signals(args)


def run_once(signals, args):
    positions = read_json(POSITIONS, {})
    watch = watcher.read_json(watcher.WATCHLIST, {})
    candidates = []
    for signal in signals:
        symbol = signal.get("symbol")
        if signal.get("action") == "EXIT":
            watcher.execute_exit(signal, watch, positions, persist=False)
            continue
        if signal.get("action") == "OPEN" and symbol not in positions:
            candidates.append(signal)
    made = orders(candidates, args.equity, args.slots, args.stop_buffer, positions, args.fee_bps)
    for order in made:
        opened_at = int(time.time())
        positions[order["symbol"]] = {
            "entry": order["price"],
            "qty": order["qty"],
            "notional": order["notional"],
            "entry_fee": order["entry_fee"],
            "fee_bps": order["fee_bps"],
            "opened_at": opened_at,
            "stop": order["stop"],
        }
        watcher.append_history({**order, "opened_at": opened_at, "reason": order.get("reason", ["paper_entry"])})
        print(json.dumps(order, ensure_ascii=False))
    write_json(POSITIONS, positions)
    watcher.write_json(watcher.WATCHLIST, watch)


def demo():
    candidates = [
        {"action": "OPEN", "symbol": "AAA", "price": 2, "support": 1.8},
        {"action": "OPEN", "symbol": "CCC", "price": 4, "support": 3.6},
    ]
    result = orders(
        candidates,
        1000,
        8,
        0.01,
    )
    assert len(result) == 2
    assert result[0]["notional"] == 125
    assert result[0]["qty"] == 62.5
    assert result[0]["entry_fee"] == 0.125
    assert result[0]["fee_bps"] == 10
    assert result[0]["stop"] == 1.782
    assert result[0]["take_profit_1"] == 2.5
    assert result[0]["take_profit_2"] == 3.0
    assert orders(candidates, 1000, 1, 0.01, {"AAA": {}}) == []
    print("demo ok")


def main():
    p = argparse.ArgumentParser(description="Allocate one fixed equity slot to each OPEN signal.")
    p.add_argument("--equity", type=float, required=False, default=1000)
    p.add_argument("--slots", type=int, default=8)
    p.add_argument("--stop-buffer", type=float, default=0.01)
    p.add_argument("--fee-bps", type=float, default=float(os.getenv("FEE_BPS", "10")))
    p.add_argument("--watch", action="store_true")
    p.add_argument("--interval", type=int, default=int(os.getenv("STRATEGY_SECONDS", "5")))
    p.add_argument("--max-symbols", type=int, default=int(os.getenv("MAX_SYMBOLS", "50")))
    p.add_argument("--level-kline", default=os.getenv("LEVEL_KLINE", "15m"))
    p.add_argument("--volume-kline", default=os.getenv("VOLUME_KLINE", os.getenv("SIGNAL_KLINE", "1m")))
    p.add_argument("--min-qvol", type=float, default=float(os.getenv("MIN_QVOL", "50000")))
    p.add_argument("--vol-mult", type=float, default=float(os.getenv("VOL_MULT", "2")))
    p.add_argument("--spike-minutes", type=int, default=int(os.getenv("SPIKE_MINUTES", "3")))
    p.add_argument("--setup-only", action=argparse.BooleanOptionalAction, default=os.getenv("SETUP_ONLY", "1") != "0")
    p.add_argument("--demo", action="store_true")
    args = p.parse_args()
    if args.demo:
        demo()
        return
    if args.watch:
        while True:
            run_once(current_signals(args), args)
            time.sleep(args.interval)
    else:
        run_once(read_signals(sys.stdin), args)


if __name__ == "__main__":
    main()
