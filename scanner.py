#!/usr/bin/env python3
import argparse
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import json_store

API = "https://fapi.binance.com"
WATCHLIST = Path("watchlist.json")
POSITIONS = Path("positions.json")
MS_PER_DAY = 24 * 60 * 60 * 1000


def get_json(path, query=None):
    url = API + path
    if query:
        url += "?" + urllib.parse.urlencode(query)
    req = urllib.request.Request(url, headers={"User-Agent": "trade-monitor-binance-scanner/0.1"})
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


def days_to_delivery(symbol_info, now_ms=None):
    delivery_ms = symbol_info.get("deliveryDate")
    if not delivery_ms:
        return None
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    return (int(delivery_ms) - now_ms) / MS_PER_DAY


def is_tradable_perp(symbol_info, min_delivery_days, now_ms=None):
    if symbol_info.get("status") != "TRADING":
        return False
    if symbol_info.get("contractType") != "PERPETUAL":
        return False
    if symbol_info.get("quoteAsset") != "USDT":
        return False
    days_left = days_to_delivery(symbol_info, now_ms)
    return days_left is None or days_left > min_delivery_days


def tickers(min_delivery_days):
    now_ms = int(time.time() * 1000)
    active = {
        s["symbol"]: s
        for s in get_json("/fapi/v1/exchangeInfo")["symbols"]
        if is_tradable_perp(s, min_delivery_days, now_ms)
    }
    out = []
    for t in get_json("/fapi/v1/ticker/24hr"):
        symbol_info = active.get(t["symbol"])
        if symbol_info:
            t = {**t, "_symbol_info": symbol_info}
            out.append(t)
    return out


def bars(symbol, interval, limit):
    rows = get_json("/fapi/v1/klines", {"symbol": symbol, "interval": interval, "limit": limit})
    return [{"qvol": float(r[7])} for r in rows]


def quote_volume_growth(symbol, interval="1m", minutes=15):
    rows = bars(symbol, interval, minutes * 2)
    if len(rows) < minutes * 2:
        return None, None
    recent = rows[-minutes:]
    baseline = rows[-(minutes * 2):-minutes]
    recent_qvol = sum(r["qvol"] for r in recent)
    previous_qvol = sum(r["qvol"] for r in baseline)
    growth_pct = ((recent_qvol / previous_qvol) - 1) * 100 if previous_qvol else None
    ratio = recent_qvol / previous_qvol if previous_qvol else None
    return growth_pct, ratio, recent_qvol, previous_qvol


def add(watch, t, rank):
    symbol = t["symbol"]
    if symbol in watch:
        return None
    symbol_info = t.get("_symbol_info") or {}
    days_left = days_to_delivery(symbol_info)
    row = {
        "venue": "binance_usdt_perpetual",
        "symbol": symbol,
        "marketStatus": symbol_info.get("status"),
        "deliveryDate": symbol_info.get("deliveryDate"),
        "daysToDelivery": round(days_left, 2) if days_left is not None else None,
        "action": "SETUP",
        "setup": True,
        "rank": rank,
        "reasons": ["ranked_by_15m_quote_volume_growth"],
        "price": float(t["lastPrice"]),
        "change24h": float(t["priceChangePercent"]),
        "quoteVolume24h": float(t["quoteVolume"]),
        "volumeGrowth15mPct": t.get("volumeGrowth15mPct"),
        "volumeGrowth15mRatio": t.get("volumeGrowth15mRatio"),
        "recentQuoteVolume15m": t.get("recentQuoteVolume15m"),
        "previousQuoteVolume15m": t.get("previousQuoteVolume15m"),
        "trades24h": int(t.get("count") or 0),
        "added_at": int(time.time()),
    }
    watch[symbol] = row
    print(json.dumps(row, ensure_ascii=False))
    return row


def refresh(row, t, rank):
    symbol_info = t.get("_symbol_info") or {}
    days_left = days_to_delivery(symbol_info)
    row.update({
        "venue": "binance_usdt_perpetual",
        "symbol": t["symbol"],
        "marketStatus": symbol_info.get("status"),
        "deliveryDate": symbol_info.get("deliveryDate"),
        "daysToDelivery": round(days_left, 2) if days_left is not None else None,
        "action": "SETUP",
        "setup": True,
        "rank": rank,
        "reasons": ["ranked_by_15m_quote_volume_growth"],
        "price": float(t["lastPrice"]),
        "change24h": float(t["priceChangePercent"]),
        "quoteVolume24h": float(t["quoteVolume"]),
        "volumeGrowth15mPct": t.get("volumeGrowth15mPct"),
        "volumeGrowth15mRatio": t.get("volumeGrowth15mRatio"),
        "recentQuoteVolume15m": t.get("recentQuoteVolume15m"),
        "previousQuoteVolume15m": t.get("previousQuoteVolume15m"),
        "trades24h": int(t.get("count") or 0),
    })
    row.setdefault("added_at", int(time.time()))
    return row


def scan(limit, min_change, max_change, min_delivery_days, positions_path=POSITIONS, volume_kline="1m", volume_minutes=15, min_volume_growth_ratio=1.1):
    watch = read_json(WATCHLIST, {})
    open_symbols = set(read_json(positions_path, {}).keys())
    added = []
    next_watch = {}

    gainers = [
        t for t in tickers(min_delivery_days)
        if t["symbol"] not in open_symbols and min_change <= float(t["priceChangePercent"]) <= max_change
    ]
    ranked = []
    for t in gainers:
        try:
            volume_growth_pct, volume_growth_ratio, recent_qvol, previous_qvol = quote_volume_growth(t["symbol"], volume_kline, volume_minutes)
        except (OSError, urllib.error.URLError):
            continue
        t["volumeGrowth15mPct"] = round(volume_growth_pct or 0, 2)
        t["volumeGrowth15mRatio"] = round(volume_growth_ratio or 0, 4)
        t["recentQuoteVolume15m"] = round(recent_qvol or 0, 2)
        t["previousQuoteVolume15m"] = round(previous_qvol or 0, 2)
        if volume_growth_ratio is None or volume_growth_ratio < min_volume_growth_ratio:
            continue
        ranked.append(t)

    ranked.sort(key=lambda t: (float(t.get("volumeGrowth15mRatio") or 0), float(t.get("recentQuoteVolume15m") or 0), float(t["priceChangePercent"])), reverse=True)
    for rank, t in enumerate(ranked[:limit], start=1):
        row = refresh(watch[t["symbol"]], t, rank) if t["symbol"] in watch else add(next_watch, t, rank)
        if row:
            next_watch[t["symbol"]] = row
            added.append(row)

    write_json(WATCHLIST, next_watch)
    return added


def demo():
    now_ms = 1_000_000_000_000
    assert is_tradable_perp({"status": "TRADING", "contractType": "PERPETUAL", "quoteAsset": "USDT", "deliveryDate": now_ms + 10 * MS_PER_DAY}, 7, now_ms)
    assert not is_tradable_perp({"status": "TRADING", "contractType": "PERPETUAL", "quoteAsset": "USDT", "deliveryDate": now_ms + 7 * MS_PER_DAY}, 7, now_ms)
    assert not is_tradable_perp({"status": "SETTLING", "contractType": "PERPETUAL", "quoteAsset": "USDT", "deliveryDate": now_ms + 100 * MS_PER_DAY}, 7, now_ms)
    watch = {}
    row = add(watch, {"symbol": "ABCUSDT", "lastPrice": "1", "priceChangePercent": "15", "quoteVolume": "1000", "count": 10}, 1)
    assert row and "ABCUSDT" in watch
    assert row["action"] == "SETUP" and row["setup"] is True and row["rank"] == 1
    original_tickers = globals()["tickers"]
    original_growth = globals()["quote_volume_growth"]
    globals()["tickers"] = lambda min_delivery_days: [
        {"symbol": "OPENUSDT", "lastPrice": "1", "openPrice": "0.8", "priceChangePercent": "15", "quoteVolume": "9000000", "count": 50000},
        {"symbol": "NEWUSDT", "lastPrice": "1", "openPrice": "0.8", "priceChangePercent": "15", "quoteVolume": "8000000", "count": 50000},
        {"symbol": "FASTUSDT", "lastPrice": "1", "openPrice": "0.8", "priceChangePercent": "8", "quoteVolume": "7000000", "count": 50000},
    ]
    globals()["quote_volume_growth"] = lambda symbol, volume_kline, volume_minutes: {
        "NEWUSDT": (80.0, 1.8, 100000, 55555),
        "FASTUSDT": (300.0, 4.0, 200000, 50000),
    }.get(symbol, (20.0, 1.2, 50000, 41666))
    tmp_positions = Path(".scanner_demo_positions.json")
    tmp_watch = Path(".scanner_demo_watchlist.json")
    original_watchlist = globals()["WATCHLIST"]
    try:
        write_json(tmp_positions, {"OPENUSDT": {"qty": 1}})
        globals()["WATCHLIST"] = tmp_watch
        added = scan(10, 5, 30, 7, tmp_positions)
        assert [row["symbol"] for row in added] == ["FASTUSDT", "NEWUSDT"]
        assert "OPENUSDT" not in read_json(tmp_watch, {})
        added = scan(10, 5, 30, 7, tmp_positions, min_volume_growth_ratio=2)
        assert [row["symbol"] for row in added] == ["FASTUSDT"]
    finally:
        globals()["tickers"] = original_tickers
        globals()["quote_volume_growth"] = original_growth
        globals()["WATCHLIST"] = original_watchlist
        for path in (tmp_positions, tmp_watch):
            if path.exists():
                path.unlink()
    print("demo ok")


def main():
    p = argparse.ArgumentParser(description="Add Binance USDT perpetual 24h gainers to watchlist.json.")
    p.add_argument("--limit", type=int, default=int(os.getenv("SCAN_LIMIT", "50")))
    p.add_argument("--min-change", type=float, default=float(os.getenv("MIN_CHANGE", "5")))
    p.add_argument("--max-change", type=float, default=float(os.getenv("MAX_CHANGE", "30")))
    p.add_argument("--min-delivery-days", type=float, default=float(os.getenv("MIN_DELIVERY_DAYS", "7")))
    p.add_argument("--volume-kline", default=os.getenv("SCAN_VOLUME_KLINE", "1m"))
    p.add_argument("--volume-minutes", type=int, default=int(os.getenv("SCAN_VOLUME_MINUTES", "15")))
    p.add_argument("--min-volume-growth-ratio", type=float, default=float(os.getenv("SCAN_MIN_VOLUME_GROWTH_RATIO", "1.1")))
    p.add_argument("--interval", type=int, default=int(os.getenv("SCAN_SECONDS", "10")))
    p.add_argument("--once", action="store_true")
    p.add_argument("--demo", action="store_true")
    args = p.parse_args()
    if args.demo:
        demo()
        return
    while True:
        scan(
            args.limit,
            args.min_change,
            args.max_change,
            args.min_delivery_days,
            volume_kline=args.volume_kline,
            volume_minutes=args.volume_minutes,
            min_volume_growth_ratio=args.min_volume_growth_ratio,
        )
        if args.once:
            return
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
