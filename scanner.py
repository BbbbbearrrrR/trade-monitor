#!/usr/bin/env python3
import argparse
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://fapi.binance.com"
WATCHLIST = Path("watchlist.json")


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


def tickers():
    active = {
        s["symbol"]
        for s in get_json("/fapi/v1/exchangeInfo")["symbols"]
        if s.get("status") == "TRADING"
        and s.get("contractType") == "PERPETUAL"
        and s.get("quoteAsset") == "USDT"
    }
    return [t for t in get_json("/fapi/v1/ticker/24hr") if t["symbol"] in active]


def score(t, min_change, max_change):
    pct = float(t["priceChangePercent"])
    qvol = float(t["quoteVolume"])
    points = 0
    reasons = []
    checks = [
        (min_change <= pct <= max_change, 35, "early_24h_gain"),
        (pct <= 25, 15, "not_too_extended"),
        (qvol >= 5_000_000, 25, "liquid_volume"),
        (int(t.get("count") or 0) >= 20_000, 15, "active_trading"),
        (float(t["lastPrice"]) > float(t["openPrice"]), 10, "green_24h"),
    ]
    for ok, pts, reason in checks:
        if ok:
            points += pts
            reasons.append(reason)
    return points, reasons


def add(watch, t, score_value, reasons):
    symbol = t["symbol"]
    if symbol in watch:
        return None
    row = {
        "venue": "binance_usdt_perpetual",
        "symbol": symbol,
        "action": "SETUP",
        "setup": True,
        "score": score_value,
        "reasons": reasons,
        "price": float(t["lastPrice"]),
        "change24h": float(t["priceChangePercent"]),
        "quoteVolume24h": float(t["quoteVolume"]),
        "trades24h": int(t.get("count") or 0),
        "added_at": int(time.time()),
    }
    watch[symbol] = row
    print(json.dumps(row, ensure_ascii=False))
    return row


def refresh(row, t, score_value, reasons):
    row.update({
        "venue": "binance_usdt_perpetual",
        "symbol": t["symbol"],
        "action": "SETUP",
        "setup": True,
        "score": score_value,
        "reasons": reasons,
        "price": float(t["lastPrice"]),
        "change24h": float(t["priceChangePercent"]),
        "quoteVolume24h": float(t["quoteVolume"]),
        "trades24h": int(t.get("count") or 0),
    })
    row.setdefault("added_at", int(time.time()))
    return row


def scan(threshold, limit, min_change, max_change):
    watch = read_json(WATCHLIST, {})
    added = []
    next_watch = {}

    gainers = [t for t in tickers() if min_change <= float(t["priceChangePercent"]) <= max_change]
    gainers.sort(key=lambda t: (float(t["quoteVolume"]), float(t["priceChangePercent"])), reverse=True)
    for t in gainers[:limit]:
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
    points, reasons = score({"priceChangePercent": "15", "quoteVolume": "8000000", "count": 50000, "lastPrice": "2", "openPrice": "1"}, 3, 30)
    assert points >= 90 and "early_24h_gain" in reasons
    points, _ = score({"priceChangePercent": "80", "quoteVolume": "8000000", "count": 50000, "lastPrice": "2", "openPrice": "1"}, 3, 30)
    assert points < 70
    watch = {}
    row = add(watch, {"symbol": "ABCUSDT", "lastPrice": "1", "priceChangePercent": "15", "quoteVolume": "1000", "count": 10}, 70, ["early_24h_gain"])
    assert row and "ABCUSDT" in watch
    assert row["action"] == "SETUP" and row["setup"] is True
    print("demo ok")


def main():
    p = argparse.ArgumentParser(description="Add Binance USDT perpetual 24h gainers to watchlist.json.")
    p.add_argument("--threshold", type=int, default=int(os.getenv("SCAN_THRESHOLD", "70")))
    p.add_argument("--limit", type=int, default=int(os.getenv("SCAN_LIMIT", "30")))
    p.add_argument("--min-change", type=float, default=float(os.getenv("MIN_CHANGE", "10")))
    p.add_argument("--max-change", type=float, default=float(os.getenv("MAX_CHANGE", "30")))
    p.add_argument("--interval", type=int, default=int(os.getenv("SCAN_SECONDS", "600")))
    p.add_argument("--once", action="store_true")
    p.add_argument("--demo", action="store_true")
    args = p.parse_args()
    if args.demo:
        demo()
        return
    while True:
        scan(args.threshold, args.limit, args.min_change, args.max_change)
        if args.once:
            return
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
