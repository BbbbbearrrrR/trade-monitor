#!/usr/bin/env python3
import argparse
import functools
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import binance_live
import json_store

API = "https://fapi.binance.com"
WATCHLIST = Path("watchlist.json")
POSITIONS = Path("positions.json")
HISTORY = Path("trade_history.json")
SIGNALS = Path("signals.json")
TAKE_PROFIT_MULT = 1.02


def get_json(path, query=None):
    url = API + path
    if query:
        url += "?" + urllib.parse.urlencode(query)
    req = urllib.request.Request(url, headers={"User-Agent": "trade-monitor-binance-watcher/0.1"})
    last = None
    for _ in range(3):
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read().decode("utf-8"))
        except (OSError, urllib.error.URLError) as e:
            last = e
            time.sleep(1)
    raise last


def read_json(path, default):
    return json_store.read_json(path, default)


def write_json(path, value):
    json_store.write_json(path, value)


def append_history(event, path=HISTORY, limit=1000):
    history = read_json(path, [])
    if event.get("action") == "CLOSE":
        reason = tuple(event.get("reason") or event.get("reasons") or [])
        closed_at = int(event.get("closed_at") or time.time())
        for row in reversed(history[-20:]):
            row_reason = tuple(row.get("reason") or row.get("reasons") or [])
            row_closed_at = int(row.get("closed_at") or 0)
            if (
                row.get("action") == "CLOSE"
                and row.get("symbol") == event.get("symbol")
                and row.get("entry") == event.get("entry")
                and row.get("qty") == event.get("qty")
                and row_reason == reason
                and abs(closed_at - row_closed_at) <= 180
            ):
                return
    history.append(event)
    if len(history) > limit:
        history = history[-limit:]
    write_json(path, history)


def bars(symbol, interval, limit):
    rows = get_json("/fapi/v1/klines", {"symbol": symbol, "interval": interval, "limit": limit})
    return [{"t": int(r[0] / 1000), "o": float(r[1]), "h": float(r[2]), "l": float(r[3]), "c": float(r[4]), "vol": float(r[5]), "qvol": float(r[7]), "trades": int(r[8])} for r in rows]


@functools.lru_cache(maxsize=1)
def exchange_symbols():
    return {s["symbol"]: s for s in get_json("/fapi/v1/exchangeInfo")["symbols"]}


def symbol_info(symbol):
    return exchange_symbols().get(symbol)


def symbol_status(symbol):
    info = symbol_info(symbol)
    return info.get("status") if info else "UNKNOWN"


def mark_price(symbol):
    data = get_json("/fapi/v1/ticker/price", {"symbol": symbol})
    if not data.get("price"):
        raise ValueError(f"{symbol} has no futures ticker price; status={symbol_status(symbol)}")
    return float(data["price"])


def exit_order(symbol, position, signal):
    price = signal.get("price") or mark_price(symbol)
    qty = float(signal.get("qty") or position.get("qty") or 0)
    position_qty = float(position.get("qty") or 0)
    entry = float(position.get("entry") or 0)
    fee_bps = float(position.get("fee_bps") or 0)
    entry_fee = float(position.get("entry_fee") or 0)
    allocated_entry_fee = entry_fee * (qty / position_qty) if position_qty else entry_fee
    notional = round(price * qty, 8)
    exit_fee = notional * fee_bps / 10000.0
    return {
        "action": "CLOSE",
        "symbol": symbol,
        "price": price,
        "qty": qty,
        "notional": notional,
        "entry": entry,
        "gross_pnl": round((price - entry) * qty, 8),
        "entry_fee": round(allocated_entry_fee, 8),
        "exit_fee": round(exit_fee, 8),
        "fee": round(allocated_entry_fee + exit_fee, 8),
        "fee_bps": fee_bps,
        "reason": signal.get("reasons", []),
        "closed_at": int(time.time()),
    }


def execute_partial_exit(signal, positions=None, persist=True, record_history=True):
    symbol = signal.get("symbol")
    if not symbol:
        return None

    own_positions = positions is None
    positions = read_json(POSITIONS, {}) if own_positions else positions
    position = positions.get(symbol)
    if not position:
        return None

    qty = min(float(signal.get("qty") or 0), float(position.get("qty") or 0))
    if qty <= 0:
        return None

    live_result = None
    if binance_live.live_enabled():
        try:
            live_result = binance_live.close_long(symbol, qty, signal.get("price"))
        except binance_live.BinanceLiveError as exc:
            print(json.dumps({
                "action": "SKIP",
                "symbol": symbol,
                "reason": ["live_close_failed"],
                "error": str(exc),
            }, ensure_ascii=False), file=sys.stderr)
            return None
        if live_result.get("avg_price"):
            signal = {**signal, "price": live_result["avg_price"]}
        if live_result.get("executed_qty"):
            qty = min(float(live_result["executed_qty"]), float(position.get("qty") or 0))

    order = exit_order(symbol, position, {**signal, "qty": qty})
    if live_result:
        order.update(live_result)
    remaining_qty = round(float(position.get("qty") or 0) - qty, 8)
    if remaining_qty <= 0:
        positions.pop(symbol, None)
    else:
        position["qty"] = remaining_qty
        entry = float(position.get("entry") or 0)
        position["notional"] = round(entry * remaining_qty, 8)
        position["entry_fee"] = round(float(position.get("entry_fee") or 0) - float(order.get("entry_fee") or 0), 8)
        leverage = max(1.0, float(position.get("leverage") or 1))
        position["margin"] = round(position["notional"] / leverage, 8)

    if record_history:
        append_history(order)
    if persist or own_positions:
        write_json(POSITIONS, positions)
    print(json.dumps(order, ensure_ascii=False))
    return order


def take_profit_signals(positions):
    out = []
    for symbol, position in positions.items():
        try:
            price = mark_price(symbol)
        except Exception:
            continue
        qty = float(position.get("qty") or 0)
        if qty <= 0:
            continue
        entry = float(position.get("entry") or 0)
        if entry <= 0:
            continue
        take_profit = round(entry * TAKE_PROFIT_MULT, 8)
        if price >= take_profit:
            out.append({
                "action": "EXIT",
                "symbol": symbol,
                "price": price,
                "qty": qty,
                "take_profit": take_profit,
                "reasons": ["take_profit_2pct"],
            })
    return out


def stop_loss_signals(positions):
    out = []
    for symbol, position in positions.items():
        stop = position.get("stop")
        if not stop:
            continue
        try:
            price = mark_price(symbol)
        except Exception:
            continue
        qty = float(position.get("qty") or 0)
        if qty <= 0:
            continue
        if price <= float(stop):
            out.append({
                "action": "EXIT",
                "symbol": symbol,
                "price": price,
                "qty": qty,
                "reasons": ["stop_loss"],
            })
    return out


def timeout_signals(positions, timeout_seconds, now=None):
    if timeout_seconds <= 0:
        return []
    now = int(time.time()) if now is None else int(now)
    out = []
    for symbol, position in positions.items():
        opened_at = position.get("opened_at")
        if not opened_at:
            continue
        qty = float(position.get("qty") or 0)
        if qty <= 0:
            continue
        age_seconds = now - int(opened_at)
        if age_seconds < timeout_seconds:
            continue
        try:
            price = mark_price(symbol)
        except Exception:
            price = None
        out.append({
            "action": "EXIT",
            "symbol": symbol,
            "price": price,
            "qty": qty,
            "age_seconds": age_seconds,
            "timeout_seconds": timeout_seconds,
            "reasons": ["position_timeout"],
        })
    return out


def execute_exit(signal, watch=None, positions=None, persist=True, record_history=True):
    symbol = signal.get("symbol")
    if not symbol:
        return None

    own_watch = watch is None
    own_positions = positions is None
    watch = read_json(WATCHLIST, {}) if own_watch else watch
    positions = read_json(POSITIONS, {}) if own_positions else positions

    watch.pop(symbol, None)
    position = positions.get(symbol)
    live_result = None
    if position and binance_live.live_enabled():
        qty = float(signal.get("qty") or position.get("qty") or 0)
        try:
            live_result = binance_live.close_long(symbol, qty, signal.get("price"))
        except binance_live.BinanceLiveError as exc:
            print(json.dumps({
                "action": "SKIP",
                "symbol": symbol,
                "reason": ["live_close_failed"],
                "error": str(exc),
            }, ensure_ascii=False), file=sys.stderr)
            return None
        if live_result.get("avg_price"):
            signal = {**signal, "price": live_result["avg_price"]}
        if live_result.get("executed_qty"):
            signal = {**signal, "qty": min(float(live_result["executed_qty"]), float(position.get("qty") or 0))}

    position = positions.pop(symbol, None)
    order = exit_order(symbol, position, signal) if position else None
    if order and live_result:
        order.update(live_result)

    if persist:
        write_json(WATCHLIST, watch)
        write_json(POSITIONS, positions)
    if order and record_history:
        append_history(order)
    if order:
        print(json.dumps(order, ensure_ascii=False))
    return order


def structure(rows, lookback=15, pivot_width=2):
    base = rows[-lookback:]
    if not base:
        return {"support": None, "resistance": None}

    swing_highs = []
    swing_lows = []
    for i in range(pivot_width, len(base) - pivot_width):
        left = base[i - pivot_width:i]
        right = base[i + 1:i + 1 + pivot_width]
        high = base[i]["h"]
        low = base[i]["l"]
        if all(high > r["h"] for r in left + right):
            swing_highs.append(high)
        if all(low < r["l"] for r in left + right):
            swing_lows.append(low)

    confirmed = base[:-pivot_width] if len(base) > pivot_width else base
    support = swing_lows[-1] if swing_lows else min(r["l"] for r in confirmed)
    resistance = swing_highs[-1] if swing_highs else max(r["h"] for r in confirmed)
    return {
        "support": support,
        "resistance": resistance,
    }


def is_setup(row):
    value = str(row.get("action") or row.get("status") or row.get("state") or "").lower()
    if value == "setup" or row.get("setup") is True:
        return True
    return "setup" in [str(r).lower() for r in row.get("reasons", [])]


def interval_minutes(interval):
    unit = interval[-1]
    value = int(interval[:-1])
    if unit == "m":
        return value
    if unit == "h":
        return value * 60
    if unit == "d":
        return value * 1440
    raise ValueError(f"unsupported interval: {interval}")


def volume_spike_signal(levels, rows, min_qvol, vol_mult, spike_minutes, volume_kline, breakout_buffer_pct=0.0):
    kline_minutes = interval_minutes(volume_kline)
    spike_bars = max(1, int(round(spike_minutes / kline_minutes)))
    prev_bars = 20
    if len(rows) < prev_bars + spike_bars:
        return "WATCH", ["warming_up"], None, None

    last = rows[-1]
    recent = rows[-spike_bars:]
    prev = rows[-(prev_bars + spike_bars):-spike_bars]
    recent_qvol = sum(r["qvol"] for r in recent)
    recent_avg_qvol = recent_qvol / spike_bars
    baseline_avg_qvol = sum(r["qvol"] for r in prev) / len(prev)
    min_avg_qvol = min_qvol * (kline_minutes / 15)
    threshold_avg_qvol = max(min_avg_qvol, baseline_avg_qvol * vol_mult)
    volume_ratio = recent_avg_qvol / baseline_avg_qvol if baseline_avg_qvol else 0

    resistance = levels.get("resistance")
    breakout_price = resistance * (1 + breakout_buffer_pct / 100.0) if resistance else None
    broke_resistance = bool(breakout_price and last["c"] > breakout_price)
    bullish_spike = last["c"] > recent[0]["o"] and last["c"] >= last["o"]

    if levels.get("support") and last["c"] < levels["support"]:
        return "EXIT", ["structure_break"], volume_ratio, recent_qvol
    if broke_resistance and recent_avg_qvol >= threshold_avg_qvol and bullish_spike:
        return "OPEN", ["setup", "resistance_break", f"{spike_bars * kline_minutes}m_volume_spike", "bullish_spike"], volume_ratio, recent_qvol
    if broke_resistance and recent_avg_qvol >= threshold_avg_qvol:
        return "SETUP", ["setup", "resistance_break", f"{spike_bars * kline_minutes}m_volume_spike", "waiting_bullish_spike"], volume_ratio, recent_qvol
    if broke_resistance:
        return "SETUP", ["setup", "resistance_break", "waiting_volume"], volume_ratio, recent_qvol
    if recent_avg_qvol >= threshold_avg_qvol:
        return "SETUP", ["setup", f"{spike_bars * kline_minutes}m_volume_spike", "waiting_breakout"], volume_ratio, recent_qvol
    return "SETUP", ["setup", "waiting_breakout", "waiting_volume"], volume_ratio, recent_qvol


def signal_for_row(symbol, row, level_kline, volume_kline, min_qvol, vol_mult, spike_minutes, setup_only=True, breakout_buffer_pct=0.2):
    if setup_only and not is_setup(row):
        return None
    level_rows = bars(symbol, level_kline, 16)
    spike_bars = max(1, int(round(spike_minutes / interval_minutes(volume_kline))))
    volume_rows = bars(symbol, volume_kline, 20 + spike_bars)
    levels = structure(level_rows[:-1])
    action, reasons, volume_ratio, recent_qvol = volume_spike_signal(levels, volume_rows, min_qvol, vol_mult, spike_minutes, volume_kline, breakout_buffer_pct)
    last = volume_rows[-1]
    breakout_pct = (last["c"] / levels["resistance"] - 1) * 100 if levels["resistance"] else 0
    return {
        "action": action,
        "symbol": symbol,
        "price": last["c"],
        "support": levels["support"],
        "resistance": levels["resistance"],
        "breakout_pct": round(breakout_pct, 2),
        "breakout_buffer_pct": breakout_buffer_pct,
        "qvol": round(recent_qvol) if recent_qvol is not None else None,
        "volume_ratio": round(volume_ratio, 2) if volume_ratio is not None else None,
        "volume_window_minutes": spike_bars * interval_minutes(volume_kline),
        "reasons": reasons,
        "source_rank": row.get("rank"),
        "source_change24h": row.get("change24h"),
        "source_volume_growth_15m_pct": row.get("volumeGrowth15mPct"),
        "source_volume_growth_15m_ratio": row.get("volumeGrowth15mRatio"),
    }


def current_signals(args):
    watch = read_json(WATCHLIST, {})
    rows_to_watch = sorted(watch.items(), key=lambda kv: (kv[1].get("rank") or 999999, -float(kv[1].get("volumeGrowth15mRatio") or 0)))
    out = []
    for symbol, row in rows_to_watch:
        try:
            signal_row = signal_for_row(
                symbol,
                row,
                args.level_kline,
                args.volume_kline,
                args.min_qvol,
                args.vol_mult,
                args.spike_minutes,
                args.setup_only,
                getattr(args, "breakout_buffer_pct", 0.2),
            )
        except (OSError, urllib.error.URLError) as e:
            print(f"skip {symbol}: {e}", file=sys.stderr)
            continue
        if signal_row:
            out.append(signal_row)
        time.sleep(0.2)
    return out


def watch_once(level_kline, volume_kline, min_qvol, vol_mult, spike_minutes, setup_only=True, position_timeout_seconds=3600, breakout_buffer_pct=0.2):
    watch = read_json(WATCHLIST, {})
    positions = read_json(POSITIONS, {})
    args = type("Args", (), {
        "level_kline": level_kline,
        "volume_kline": volume_kline,
        "min_qvol": min_qvol,
        "vol_mult": vol_mult,
        "spike_minutes": spike_minutes,
        "setup_only": setup_only,
        "breakout_buffer_pct": breakout_buffer_pct,
    })()
    changed = False
    for stop_signal in stop_loss_signals(positions):
        print(json.dumps(stop_signal, ensure_ascii=False))
        execute_exit(stop_signal, watch, positions, persist=False)
        changed = True
    for timeout_signal in timeout_signals(positions, position_timeout_seconds):
        if timeout_signal["symbol"] not in positions:
            continue
        print(json.dumps(timeout_signal, ensure_ascii=False))
        execute_exit(timeout_signal, watch, positions, persist=False)
        changed = True
    for tp_signal in take_profit_signals(positions):
        if tp_signal["symbol"] not in positions:
            continue
        print(json.dumps(tp_signal, ensure_ascii=False))
        execute_exit(tp_signal, watch, positions, persist=False)
        changed = True
    visible_signals = []
    for out in current_signals(args):
        print(json.dumps(out, ensure_ascii=False))
        if out["action"] == "EXIT":
            execute_exit(out, watch, positions, persist=False)
            changed = True
        else:
            visible_signals.append(out)
    write_json(SIGNALS, {
        "updated_at": int(time.time()),
        "signals": visible_signals,
    })
    if changed:
        write_json(WATCHLIST, watch)
        write_json(POSITIONS, positions)


def demo():
    rows15 = [{"t": i, "o": 1.35, "h": 1.38, "l": 1.3, "c": 1.35, "vol": 1, "qvol": 1000, "trades": 10} for i in range(15)]
    rows15[4]["h"] = 1.42
    rows15[8]["l"] = 1.28
    current = {"t": 16, "o": 1.36, "h": 2.0, "l": 0.5, "c": 1.38, "vol": 1, "qvol": 3500, "trades": 50}
    assert structure(rows15 + [current])["support"] == 1.28
    assert structure(rows15)["support"] == 1.28
    assert structure(rows15)["resistance"] == 1.42

    rows1m = [{"t": i, "o": 1.36, "h": 1.38, "l": 1.35, "c": 1.36, "vol": 1, "qvol": 1000, "trades": 10} for i in range(20)]
    rows1m += [{"t": 21 + i, "o": 1.36, "h": 1.45, "l": 1.35, "c": 1.43, "vol": 1, "qvol": 3500, "trades": 50} for i in range(3)]
    action, reasons, ratio, recent_qvol = volume_spike_signal(structure(rows15), rows1m, 1000, 3, 3, "1m", 0.2)
    assert action == "OPEN" and "resistance_break" in reasons and "3m_volume_spike" in reasons and "bullish_spike" in reasons and ratio == 3.5 and recent_qvol == 10500
    bearish_spike_rows = rows1m[:-3] + [{"t": 21 + i, "o": 1.48, "h": 1.5, "l": 1.42, "c": 1.43, "vol": 1, "qvol": 3500, "trades": 50} for i in range(3)]
    action, reasons, _, _ = volume_spike_signal(structure(rows15), bearish_spike_rows, 1000, 3, 3, "1m", 0.2)
    assert action == "SETUP" and "waiting_bullish_spike" in reasons
    touch_rows = rows1m[:-3] + [{"t": 21 + i, "o": 1.36, "h": 1.43, "l": 1.35, "c": 1.421, "vol": 1, "qvol": 3500, "trades": 50} for i in range(3)]
    action, reasons, _, _ = volume_spike_signal(structure(rows15), touch_rows, 1000, 3, 3, "1m", 0.2)
    assert action == "SETUP" and "waiting_breakout" in reasons
    no_break_rows = rows1m[:-3] + [{"t": 21 + i, "o": 1.36, "h": 1.39, "l": 1.35, "c": 1.38, "vol": 1, "qvol": 3500, "trades": 50} for i in range(3)]
    action, reasons, _, _ = volume_spike_signal(structure(rows15), no_break_rows, 1000, 3, 3, "1m")
    assert action == "SETUP" and "waiting_breakout" in reasons
    assert is_setup({"action": "SETUP"})
    assert is_setup({"setup": True})
    assert not is_setup({"action": "HOLD"})
    watch = {"AAA": {"symbol": "AAA"}}
    positions = {"AAA": {"entry": 2, "qty": 10, "notional": 20}}
    order = execute_exit({"action": "EXIT", "symbol": "AAA", "price": 1.8, "reasons": ["structure_break"]}, watch, positions, persist=False, record_history=False)
    assert order["action"] == "CLOSE" and order["gross_pnl"] == -2
    assert "AAA" not in watch and "AAA" not in positions
    positions = {"AAA": {"entry": 2, "qty": 10, "notional": 20, "leverage": 1}}
    order = execute_partial_exit({"symbol": "AAA", "price": 2.3, "qty": 5, "reasons": ["take_profit_1"]}, positions, persist=False, record_history=False)
    assert order["gross_pnl"] == 1.5 and positions["AAA"]["qty"] == 5
    positions = {"AAA": {"entry": 2, "qty": 10, "notional": 20, "entry_fee": 0.2, "fee_bps": 10, "leverage": 1}}
    order = execute_partial_exit({"symbol": "AAA", "price": 2.3, "qty": 5, "reasons": ["take_profit_1"]}, positions, persist=False, record_history=False)
    assert order["entry_fee"] == 0.1 and positions["AAA"]["entry_fee"] == 0.1
    assert stop_loss_signals({"AAA": {"entry": 2, "qty": 10, "stop": 2.1}}) == []
    original_mark_price = mark_price
    globals()["mark_price"] = lambda symbol: 2.1
    try:
        timed_out = timeout_signals({"AAA": {"opened_at": 100, "qty": 10}}, 10800, now=10901)
        assert timed_out and timed_out[0]["reasons"] == ["position_timeout"]
        assert timeout_signals({"AAA": {"opened_at": 100, "qty": 10}}, 10800, now=10899) == []
        assert timeout_signals({"AAA": {"opened_at": 100, "qty": 10}}, 0, now=20000) == []
    finally:
        globals()["mark_price"] = original_mark_price
    print("demo ok")

def main():
    p = argparse.ArgumentParser(description="Monitor Binance watchlist.json for K-line and volume signals.")
    p.add_argument("--level-kline", default=os.getenv("LEVEL_KLINE", "15m"))
    p.add_argument("--volume-kline", default=os.getenv("VOLUME_KLINE", os.getenv("SIGNAL_KLINE", "1m")))
    p.add_argument("--min-qvol", type=float, default=float(os.getenv("MIN_QVOL", "50000")))
    p.add_argument("--vol-mult", type=float, default=float(os.getenv("VOL_MULT", "2")))
    p.add_argument("--spike-minutes", type=int, default=int(os.getenv("SPIKE_MINUTES", "3")))
    p.add_argument("--breakout-buffer-pct", type=float, default=float(os.getenv("BREAKOUT_BUFFER_PCT", "0.2")))
    p.add_argument("--setup-only", action=argparse.BooleanOptionalAction, default=os.getenv("SETUP_ONLY", "1") != "0")
    p.add_argument("--interval", type=int, default=int(os.getenv("WATCH_SECONDS", "15")))
    p.add_argument("--position-timeout-seconds", type=int, default=int(os.getenv("POSITION_TIMEOUT_SECONDS", "3600")))
    p.add_argument("--once", action="store_true")
    p.add_argument("--demo", action="store_true")
    args = p.parse_args()
    if args.demo:
        demo()
        return
    while True:
        watch_once(
            args.level_kline,
            args.volume_kline,
            args.min_qvol,
            args.vol_mult,
            args.spike_minutes,
            args.setup_only,
            args.position_timeout_seconds,
            args.breakout_buffer_pct,
        )
        if args.once:
            return
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
