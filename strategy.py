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


def position_margin(position):
    notional = float(position.get("notional") or (float(position.get("entry") or 0) * float(position.get("qty") or 0)))
    leverage = max(1.0, float(position.get("leverage") or 1))
    return float(position.get("margin") or (notional / leverage))


def used_cash(positions):
    return sum(position_margin(p) + float(p.get("entry_fee") or 0) for p in (positions or {}).values())


def choose_leverage(notional, fee, available_cash, base_leverage, max_leverage):
    leverage = max(1.0, float(base_leverage))
    max_leverage = max(leverage, float(max_leverage))
    while leverage <= max_leverage:
        margin = notional / leverage
        if margin + fee <= available_cash:
            return leverage, margin
        leverage *= 2
    return None, None


def orders(candidates, equity, slots, stop_buffer, positions=None, fee_bps=10, base_leverage=1, max_leverage=8):
    open_slots = max(0, slots - len(positions or {}))
    if open_slots <= 0:
        return []
    target_notional = round(equity / slots, 2)
    available_cash = equity - used_cash(positions)
    out = []
    for s in candidates:
        if len(out) >= open_slots:
            break
        entry_fee = fee_usdt(target_notional, fee_bps)
        leverage, margin = choose_leverage(target_notional, entry_fee, available_cash, base_leverage, max_leverage)
        if leverage is None:
            print(json.dumps({
                "action": "SKIP",
                "symbol": s.get("symbol"),
                "reason": ["insufficient_margin"],
                "available_cash": round(available_cash, 8),
                "target_notional": target_notional,
                "max_leverage": float(max_leverage),
            }, ensure_ascii=False), file=sys.stderr)
            continue
        order = {
            "action": "BUY",
            "symbol": s["symbol"],
            "price": s["price"],
            "notional": target_notional,
            "qty": round(target_notional / s["price"], 8),
            "margin": round(margin, 8),
            "entry_fee": entry_fee,
            "fee_bps": float(fee_bps),
            "leverage": int(leverage) if leverage.is_integer() else leverage,
            "stop": round(s["support"] * (1 - stop_buffer), 8) if s.get("support") else None,
            "take_profit_1": round(s["price"] * 1.15, 8),
            "take_profit_2": round(s["price"] * 1.30, 8),
            "take_profit_qty_pct": [50, 50],
        }
        if leverage > base_leverage:
            order["reason"] = [f"auto_leverage_{order['leverage']}x"]
        out.append(order)
        available_cash -= margin + entry_fee
    return out


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
    made = orders(candidates, args.equity, args.slots, args.stop_buffer, positions, args.fee_bps, args.leverage, args.max_leverage)
    for order in made:
        opened_at = int(time.time())
        positions[order["symbol"]] = {
            "entry": order["price"],
            "qty": order["qty"],
            "notional": order["notional"],
            "margin": order["margin"],
            "entry_fee": order["entry_fee"],
            "fee_bps": order["fee_bps"],
            "leverage": order["leverage"],
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
    assert result[0]["margin"] == 125
    assert result[0]["qty"] == 62.5
    assert result[0]["entry_fee"] == 0.125
    assert result[0]["fee_bps"] == 10
    assert result[0]["leverage"] == 1
    assert result[0]["stop"] == 1.782
    assert result[0]["take_profit_1"] == 2.3
    assert result[0]["take_profit_2"] == 2.6
    assert orders(candidates, 1000, 1, 0.01, {"AAA": {}}) == []
    nearly_full = {str(i): {"notional": 125, "margin": 125, "entry_fee": 0.125, "leverage": 1} for i in range(7)}
    result = orders([{"action": "OPEN", "symbol": "BBB", "price": 1, "support": 0.9}], 1000, 8, 0.01, nearly_full)
    assert result[0]["leverage"] == 2 and result[0]["margin"] == 62.5
    print("demo ok")


def main():
    p = argparse.ArgumentParser(description="Allocate one fixed equity slot to each OPEN signal.")
    p.add_argument("--equity", type=float, required=False, default=1000)
    p.add_argument("--slots", type=int, default=8)
    p.add_argument("--stop-buffer", type=float, default=0.01)
    p.add_argument("--fee-bps", type=float, default=float(os.getenv("FEE_BPS", "10")))
    p.add_argument("--leverage", type=float, default=float(os.getenv("LEVERAGE", "1")))
    p.add_argument("--max-leverage", type=float, default=float(os.getenv("MAX_LEVERAGE", "8")))
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
