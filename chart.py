#!/usr/bin/env python3
import argparse
import html
from pathlib import Path

import watcher


def x(i, n, w):
    return 40 + i * ((w - 80) / max(1, n - 1))


def y(price, lo, hi, h):
    return 20 + (hi - price) * ((h - 60) / max(hi - lo, 1e-12))


def render(symbol, kline, limit, windows, out):
    rows = watcher.bars(symbol, kline, max(limit, max(windows)))
    view = rows[-limit:]
    levels = watcher.structures(rows, windows)
    lo = min([r["l"] for r in view] + [s["support"] for s in levels])
    hi = max([r["h"] for r in view] + [s["resistance"] for s in levels])
    w, h = 1200, 640
    candle_w = max(2, int((w - 80) / len(view) * 0.65))

    parts = [
        "<!doctype html><meta charset='utf-8'>",
        f"<title>{html.escape(symbol)} structure</title>",
        "<body style='margin:0;background:#111;color:#ddd;font-family:Arial,sans-serif'>",
        f"<h3 style='margin:12px 16px'>{html.escape(symbol)} {html.escape(kline)} structure check</h3>",
        f"<svg width='{w}' height='{h}' viewBox='0 0 {w} {h}' style='display:block;width:100%;height:auto'>",
        "<rect width='100%' height='100%' fill='#111'/>",
    ]

    for i, r in enumerate(view):
        cx = x(i, len(view), w)
        yo, yh, yl, yc = y(r["o"], lo, hi, h), y(r["h"], lo, hi, h), y(r["l"], lo, hi, h), y(r["c"], lo, hi, h)
        color = "#22c55e" if r["c"] >= r["o"] else "#ef4444"
        top, height = min(yo, yc), max(1, abs(yc - yo))
        parts += [
            f"<line x1='{cx:.1f}' y1='{yh:.1f}' x2='{cx:.1f}' y2='{yl:.1f}' stroke='{color}' stroke-width='1'/>",
            f"<rect x='{cx - candle_w / 2:.1f}' y='{top:.1f}' width='{candle_w}' height='{height:.1f}' fill='{color}'/>",
        ]

    colors = ["#facc15", "#38bdf8", "#f472b6", "#a78bfa"]
    for i, s in enumerate(levels):
        color = colors[i % len(colors)]
        for name in ("support", "resistance"):
            yy = y(s[name], lo, hi, h)
            dash = "6 5" if name == "support" else "none"
            parts += [
                f"<line x1='40' y1='{yy:.1f}' x2='{w - 40}' y2='{yy:.1f}' stroke='{color}' stroke-width='1.5' stroke-dasharray='{dash}'/>",
                f"<text x='{w - 34}' y='{yy + 4:.1f}' fill='{color}' font-size='12'>{s['window']} {name} {s[name]:g}</text>",
            ]

    last = view[-1]["c"]
    parts += [
        f"<text x='42' y='18' fill='#ddd' font-size='13'>last {last:g}</text>",
        "</svg>",
        "</body>",
    ]
    out.parent.mkdir(exist_ok=True)
    out.write_text("\n".join(parts), "utf-8")
    return out


def main():
    p = argparse.ArgumentParser(description="Render candles with watcher structure levels.")
    p.add_argument("symbol")
    p.add_argument("--kline", default="5m")
    p.add_argument("--bars", type=int, default=120)
    p.add_argument("--windows", default="20,60,120")
    p.add_argument("--out")
    args = p.parse_args()
    windows = [int(x) for x in args.windows.split(",") if x.strip()]
    out = Path(args.out or f"charts/{args.symbol}_{args.kline}.html")
    print(render(args.symbol.upper(), args.kline, args.bars, windows, out))


if __name__ == "__main__":
    main()
