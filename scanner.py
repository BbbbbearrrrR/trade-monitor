#!/usr/bin/env python3
import argparse
import json
import math
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

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
    return json.loads(path.read_text("utf-8")) if path.exists() else default


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), "utf-8")


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


def quote_volume_growth_24h(symbol, interval="1h", hours=24):
    rows = bars(symbol, interval, hours * 2)
    if len(rows) < hours * 2:
        return None, None
    recent = rows[-hours:]
    baseline = rows[-(hours * 2):-hours]
    recent_qvol = sum(r["qvol"] for r in recent)
    previous_qvol = sum(r["qvol"] for r in baseline)
    growth_pct = ((recent_qvol / previous_qvol) - 1) * 100 if previous_qvol else None
    return growth_pct, recent_qvol


def score(t, min_change, max_change):
    pct = float(t["priceChangePercent"])
    qvol = float(t["quoteVolume"])
    last = float(t["lastPrice"])
    opened = float(t["openPrice"])
    volume_growth_pct = float(t.get("volumeGrowthPct") or 0)
    span = max(1.0, max_change - min_change)
    pct_pos = max(0.0, min(1.0, (pct - min_change) / span))
    volume_growth_score = max(0.0, min(45.0, volume_growth_pct / 200.0 * 45.0))
    change_score = max(0.0, min(35.0, pct_pos * 35.0))
    liquidity_score = max(0.0, min(10.0, math.log10(max(qvol, 1) / 5_000_000) / math.log10(20) * 10.0))
    green_score = 10.0 if last > opened else 0.0
    extension_penalty = max(0.0, pct - 25.0) * 1.5
    points = round(max(0.0, min(100.0, volume_growth_score + change_score + liquidity_score + green_score - extension_penalty)))
    reasons = []
    if volume_growth_score >= 30:
        reasons.append("strong_quote_volume_growth")
    elif volume_growth_score >= 15:
        reasons.append("moderate_quote_volume_growth")
    if change_score >= 24:
        reasons.append("strong_24h_gain")
    elif change_score >= 10:
        reasons.append("early_24h_gain")
    if liquidity_score >= 5:
        reasons.append("liquid_24h_volume")
    if green_score:
        reasons.append("green_24h")
    if extension_penalty:
        reasons.append("extension_penalty")
    return points, reasons


def add(watch, t, score_value, reasons):
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
        "score": score_value,
        "reasons": reasons,
        "price": float(t["lastPrice"]),
        "change24h": float(t["priceChangePercent"]),
        "quoteVolume24h": float(t["quoteVolume"]),
        "volumeGrowthPct": t.get("volumeGrowthPct"),
        "recentQuoteVolume": t.get("recentQuoteVolume"),
        "trades24h": int(t.get("count") or 0),
        "added_at": int(time.time()),
    }
    watch[symbol] = row
    print(json.dumps(row, ensure_ascii=False))
    return row


def refresh(row, t, score_value, reasons):
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
        "score": score_value,
        "reasons": reasons,
        "price": float(t["lastPrice"]),
        "change24h": float(t["priceChangePercent"]),
        "quoteVolume24h": float(t["quoteVolume"]),
        "volumeGrowthPct": t.get("volumeGrowthPct"),
        "recentQuoteVolume": t.get("recentQuoteVolume"),
        "trades24h": int(t.get("count") or 0),
    })
    row.setdefault("added_at", int(time.time()))
    return row


def scan(threshold, limit, min_change, max_change, min_delivery_days, positions_path=POSITIONS, volume_kline="1h", volume_hours=24):
    watch = read_json(WATCHLIST, {})
    open_symbols = set(read_json(positions_path, {}).keys())
    added = []
    next_watch = {}

    gainers = [
        t for t in tickers(min_delivery_days)
        if t["symbol"] not in open_symbols and min_change <= float(t["priceChangePercent"]) <= max_change
    ]
    gainers.sort(key=lambda t: (float(t["quoteVolume"]), float(t["priceChangePercent"])), reverse=True)
    for t in gainers[:limit]:
        try:
            volume_growth_pct, recent_qvol = quote_volume_growth_24h(t["symbol"], volume_kline, volume_hours)
        except (OSError, urllib.error.URLError):
            continue
        t["volumeGrowthPct"] = round(volume_growth_pct or 0, 2)
        t["recentQuoteVolume"] = round(recent_qvol or 0, 2)
        points, reasons = score(t, min_change, max_change)
        if points < threshold:
            continue
        row = refresh(watch[t["symbol"]], t, points, reasons) if t["symbol"] in watch else add(next_watch, t, points, reasons)
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
    points, reasons = score({"priceChangePercent": "15", "quoteVolume": "8000000", "volumeGrowthPct": 80, "count": 50000, "lastPrice": "2", "openPrice": "1"}, 3, 30)
    assert points >= 45 and "early_24h_gain" in reasons and "moderate_quote_volume_growth" in reasons
    strong_points, strong_reasons = score({"priceChangePercent": "24", "quoteVolume": "120000000", "volumeGrowthPct": 200, "count": 500000, "lastPrice": "2", "openPrice": "1"}, 3, 30)
    assert strong_points >= 90 and "strong_quote_volume_growth" in strong_reasons
    points, _ = score({"priceChangePercent": "80", "quoteVolume": "8000000", "volumeGrowthPct": 200, "count": 50000, "lastPrice": "2", "openPrice": "1"}, 3, 30)
    assert points < 70
    watch = {}
    row = add(watch, {"symbol": "ABCUSDT", "lastPrice": "1", "priceChangePercent": "15", "quoteVolume": "1000", "count": 10}, 70, ["early_24h_gain"])
    assert row and "ABCUSDT" in watch
    assert row["action"] == "SETUP" and row["setup"] is True
    original_tickers = globals()["tickers"]
    original_growth = globals()["quote_volume_growth_24h"]
    globals()["tickers"] = lambda min_delivery_days: [
        {"symbol": "OPENUSDT", "lastPrice": "1", "openPrice": "0.8", "priceChangePercent": "15", "quoteVolume": "9000000", "count": 50000},
        {"symbol": "NEWUSDT", "lastPrice": "1", "openPrice": "0.8", "priceChangePercent": "15", "quoteVolume": "8000000", "count": 50000},
    ]
    globals()["quote_volume_growth_24h"] = lambda symbol, volume_kline, volume_hours: (80.0, 100000)
    tmp_positions = Path(".scanner_demo_positions.json")
    tmp_watch = Path(".scanner_demo_watchlist.json")
    original_watchlist = globals()["WATCHLIST"]
    try:
        write_json(tmp_positions, {"OPENUSDT": {"qty": 1}})
        globals()["WATCHLIST"] = tmp_watch
        added = scan(40, 10, 3, 30, 7, tmp_positions)
        assert [row["symbol"] for row in added] == ["NEWUSDT"]
        assert "OPENUSDT" not in read_json(tmp_watch, {})
    finally:
        globals()["tickers"] = original_tickers
        globals()["quote_volume_growth_24h"] = original_growth
        globals()["WATCHLIST"] = original_watchlist
        for path in (tmp_positions, tmp_watch):
            if path.exists():
                path.unlink()
    print("demo ok")


def main():
    p = argparse.ArgumentParser(description="Add Binance USDT perpetual 24h gainers to watchlist.json.")
    p.add_argument("--threshold", type=int, default=int(os.getenv("SCAN_THRESHOLD", "60")))
    p.add_argument("--limit", type=int, default=int(os.getenv("SCAN_LIMIT", "30")))
    p.add_argument("--min-change", type=float, default=float(os.getenv("MIN_CHANGE", "10")))
    p.add_argument("--max-change", type=float, default=float(os.getenv("MAX_CHANGE", "30")))
    p.add_argument("--min-delivery-days", type=float, default=float(os.getenv("MIN_DELIVERY_DAYS", "7")))
    p.add_argument("--volume-kline", default=os.getenv("SCAN_VOLUME_KLINE", "1h"))
    p.add_argument("--volume-hours", type=int, default=int(os.getenv("SCAN_VOLUME_HOURS", "24")))
    p.add_argument("--interval", type=int, default=int(os.getenv("SCAN_SECONDS", "600")))
    p.add_argument("--once", action="store_true")
    p.add_argument("--demo", action="store_true")
    args = p.parse_args()
    if args.demo:
        demo()
        return
    while True:
        scan(
            args.threshold,
            args.limit,
            args.min_change,
            args.max_change,
            args.min_delivery_days,
            volume_kline=args.volume_kline,
            volume_hours=args.volume_hours,
        )
        if args.once:
            return
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
