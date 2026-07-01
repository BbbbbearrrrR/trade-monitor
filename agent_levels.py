#!/usr/bin/env python3
import argparse
import json
import os
import re
import urllib.request

import watcher

OPENAI_API = "https://api.openai.com/v1/responses"


def compact(rows):
    return [
        {
            "t": r["t"],
            "o": r["o"],
            "h": r["h"],
            "l": r["l"],
            "c": r["c"],
            "qv": round(r["qvol"], 2),
        }
        for r in rows
    ]


def extract_json(value):
    if isinstance(value, dict):
        if value.get("type") in ("output_text", "text") and isinstance(value.get("text"), str):
            return extract_json(value["text"])
        for v in value.values():
            found = extract_json(v)
            if found:
                return found
    if isinstance(value, list):
        for v in value:
            found = extract_json(v)
            if found:
                return found
    if isinstance(value, str):
        m = re.search(r"\{.*\}", value, re.S)
        if m:
            return json.loads(m.group(0))
    return None


def judge(symbol, kline, bars, model):
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        raise SystemExit("set OPENAI_API_KEY first")

    rows = watcher.bars(symbol, kline, bars)
    prompt = {
        "task": "Find exactly two actionable horizontal levels from OHLCV candles.",
        "symbol": symbol,
        "kline": kline,
        "rules": [
            "Return one current support and one current resistance.",
            "Use price zones humans would draw, not tiny wick noise.",
            "Resistance should be the level whose breakout with volume could trigger continuation.",
            "Support should be the nearest meaningful invalidation level below price.",
            "Prefer levels with repeated touches, compression, or recent reaction.",
            "Do not invent extra indicators. Return JSON only.",
        ],
        "output_schema": {
            "support": "number",
            "resistance": "number",
            "confidence": "low|medium|high",
            "reason": "short string",
        },
        "candles": compact(rows),
    }
    body = json.dumps({"model": model, "input": json.dumps(prompt, separators=(",", ":"))}).encode()
    req = urllib.request.Request(
        OPENAI_API,
        data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.loads(r.read().decode("utf-8"))
    out = extract_json(data)
    if not out:
        raise SystemExit(json.dumps(data, indent=2)[:2000])
    return out


def demo():
    sample = {"output": [{"content": [{"type": "output_text", "text": '{"support":1,"resistance":2,"confidence":"high","reason":"test"}'}]}]}
    assert extract_json(sample)["resistance"] == 2
    print("demo ok")


def main():
    p = argparse.ArgumentParser(description="Ask an LLM agent to mark support/resistance for one symbol.")
    p.add_argument("symbol", nargs="?")
    p.add_argument("--kline", default="5m")
    p.add_argument("--bars", type=int, default=120)
    p.add_argument("--model", default=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"))
    p.add_argument("--demo", action="store_true")
    args = p.parse_args()
    if args.demo:
        demo()
        return
    if not args.symbol:
        raise SystemExit("symbol required")
    print(json.dumps(judge(args.symbol.upper(), args.kline, args.bars, args.model), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
